# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import ipaddress

from tempest import config
from tempest.lib import decorators
from tempest.lib import exceptions as lib_exc

from networking_mlnx_tempest_plugin.tests import base

CONF = config.CONF


# Neutron allows only one flat network on each physical network, and each
# test uses InfiniBand virtual functions, of which hosts have only a few.
@decorators.serial
class PkeyGuestTest(base.BaseInfinibandGuestTest):
    """Check InfiniBand access from inside guests."""

    @decorators.idempotent_id('4507da4c-c4f1-47eb-9bac-610f62aa25ad')
    def test_vf_link_follows_port_admin_state(self):
        """Check that a guest's InfiniBand port follows admin_state_up.

        neutron-mlnx-agent only enables a virtual function's link while
        its port is ACTIVE and administratively up, so setting the port's
        admin_state_up to False takes the InfiniBand port in the guest
        DOWN, and setting it back to True makes it ACTIVE again. The
        network is the configured one.
        """
        network = self.get_infiniband_network(self.addCleanup)
        if (network['provider:network_type'] == 'vlan' and
                not self.is_vlan_binding_allowed(network['id'])):
            raise self.skipException(
                'mlnx_infiniband does not bind ports to VLAN networks here: '
                'mlnx_sdn_assist does not sync the InfiniBand network, and '
                '[networking_mlnx] allow_unenforced_pkeys is False')
        # Create the port before the server, so that the server is deleted
        # first.
        port = self.create_infiniband_port(network)
        server, ssh = self.create_guest()

        self.attach_port(server, port)
        interface = self.wait_for_ipoib_interface(ssh, port)
        self.wait_for_ib_port_state(ssh, interface, 'ACTIVE')

        self.ports_client.update_port(port['id'], admin_state_up=False)
        self.wait_for_ib_port_state(ssh, interface, 'DOWN')

        self.ports_client.update_port(port['id'], admin_state_up=True)
        self.wait_for_ib_port_state(ssh, interface, 'ACTIVE')

    @decorators.idempotent_id('2e65b85e-7e3a-45be-a9de-f73cfd10dc8f')
    def test_networks_are_isolated(self):
        """Check that guests on different VLAN networks can't communicate.

        Two VLAN networks have subnets with the same CIDR. A guest with a
        port on the first network can't reach a guest with a port on the
        second, since their ports are in different partition keys. Then
        the second guest's port is replaced with one on the first network,
        with the same IP address, which the first guest must reach.
        """
        network_a = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        if not (self.is_sdn_synced(network_a['id']) or
                (CONF.networking_mlnx.allow_unenforced_pkeys and
                 CONF.networking_mlnx.network_type == 'vlan')):
            raise self.skipException(
                'Nothing is known to keep ports in their partition keys: '
                'mlnx_sdn_assist does not sync the InfiniBand network, and '
                '[networking_mlnx] allow_unenforced_pkeys is False or '
                'network_type is not vlan')
        network_b = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        subnet = ipaddress.ip_network(CONF.networking_mlnx.subnet_cidr)
        address1, address2 = str(subnet[-3]), str(subnet[-2])
        # Create the ports before the servers, so that the servers are
        # deleted first.
        port1 = self.create_infiniband_port(
            network_a, fixed_ips=[{'ip_address': address1}])
        port2 = self.create_infiniband_port(
            network_b, fixed_ips=[{'ip_address': address2}])
        port3 = self.create_infiniband_port(
            network_a, fixed_ips=[{'ip_address': address2}])
        server1, ssh1 = self.create_guest()
        server2, ssh2 = self.create_guest()

        self.attach_port(server1, port1)
        self.attach_port(server2, port2)
        interface1 = self.wait_for_ipoib_interface(ssh1, port1)
        interface2 = self.wait_for_ipoib_interface(ssh2, port2)
        self.wait_for_ib_port_state(ssh1, interface1, 'ACTIVE')
        self.wait_for_ib_port_state(ssh2, interface2, 'ACTIVE')
        self.configure_ipoib_interface(ssh1, interface1, port1)
        self.configure_ipoib_interface(ssh2, interface2, port2)
        # Address resolution over IPoIB can take a while once the address
        # is added, so ping for longer than a single packet.
        self.assertRaises(
            lib_exc.SSHExecCommandFailed, ssh1.exec_command,
            'sudo ping -I %s -c 10 -w 20 %s' % (interface1, address2))

        # Positive control, with the same number of virtual functions.
        self.detach_port(server2, port2)
        self.wait_in_guest(
            lambda: interface2 not in ssh2.exec_command(
                base.LIST_IPOIB_INTERFACES).split(),
            'remove %s' % interface2)
        self.attach_port(server2, port3)
        interface3 = self.wait_for_ipoib_interface(ssh2, port3)
        self.wait_for_ib_port_state(ssh2, interface3, 'ACTIVE')
        self.configure_ipoib_interface(ssh2, interface3, port3)
        self.check_remote_connectivity(ssh1, address2, nic=interface1)
