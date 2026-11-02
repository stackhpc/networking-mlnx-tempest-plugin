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

from tempest.common import utils
from tempest.common import waiters
from tempest import config
from tempest.lib.common.utils import test_utils
from tempest.lib import decorators
from tempest.lib import exceptions as lib_exc
from tempest.scenario import manager

from networking_mlnx_tempest_plugin.tests import base

CONF = config.CONF

# The type of IPoIB interfaces in /sys/class/net/*/type (ARPHRD_INFINIBAND).
IPOIB_TYPE = '32'

# Print the name and hardware address of each IPoIB interface.
LIST_IPOIB_INTERFACES = (
    'for dev in /sys/class/net/*; do '
    'if [ "$(cat "$dev/type" 2>/dev/null)" = %s ]; then '
    'echo "${dev##*/} $(cat "$dev/address")"; fi; done' % IPOIB_TYPE)


def guid_matches_mac(hardware_address, mac_address):
    """Check if an IPoIB address has the GUID derived from a MAC address.

    The last 8 bytes of an IPoIB hardware address are the port GUID.
    neutron-mlnx-agent gives a virtual function the GUID derived from its
    port's MAC address, from its first and last 3 bytes; the 2 bytes in
    between may differ.
    """
    guid = hardware_address.lower().split(':')[-8:]
    mac = mac_address.lower().split(':')
    return len(guid) == 8 and guid[:3] + guid[5:] == mac


# Neutron allows only one flat network on each physical network, and each
# test uses InfiniBand virtual functions, of which hosts have only a few.
@decorators.serial
class PkeyGuestTest(base.InfinibandMixin, manager.NetworkScenarioTest):
    """Check InfiniBand access from inside guests.

    The guests need an image with InfiniBand drivers, such as mlx5_ib and
    ib_ipoib, from [networking_mlnx] guest_image_ref. Each guest has a port
    on a project network with a floating IP for SSH. The InfiniBand ports
    are attached after the guests have booted, since cloud-init may fail
    to configure an InfiniBand interface at boot.
    """

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        cls.check_networking_mlnx()
        cls.check_network_mtu()
        if not CONF.service_available.nova:
            raise cls.skipException('Nova is not available')
        if not CONF.compute_feature_enabled.interface_attach:
            raise cls.skipException('Interface attachment is not available')
        if not CONF.networking_mlnx.guest_image_ref:
            raise cls.skipException(
                '[networking_mlnx] guest_image_ref is not set')
        if not CONF.network.public_network_id:
            raise cls.skipException('[network] public_network_id is not set')
        if not CONF.network_feature_enabled.floating_ips:
            raise cls.skipException('Floating IPs are not available')
        for extension in ('router', 'security-group'):
            if not utils.is_extension_enabled(extension, 'network'):
                raise cls.skipException(
                    'The %s extension is not enabled' % extension)

    @classmethod
    def setup_clients(cls):
        super().setup_clients()
        cls.setup_infiniband_clients()
        cls.admin_servers_client = cls.os_admin.servers_client

    def setUp(self):
        super().setUp()
        self.keypair = self.create_keypair()
        self.security_group = self.create_security_group()

    def create_guest(self):
        """Boot a guest and return it and an SSH client for it."""
        server = self.create_server(
            image_id=CONF.networking_mlnx.guest_image_ref,
            flavor=(CONF.networking_mlnx.guest_flavor_ref or
                    CONF.compute.flavor_ref),
            key_name=self.keypair['name'],
            security_groups=[{'name': self.security_group['name']}],
            wait_until='ACTIVE')
        floating_ip = self.create_floating_ip(server)
        ssh = self.get_remote_client(
            floating_ip['floating_ip_address'],
            username=(CONF.networking_mlnx.guest_ssh_user or
                      CONF.validation.image_ssh_user),
            private_key=self.keypair['private_key'],
            server=server)
        return server, ssh

    def attach_port(self, server, port):
        self.interface_client.create_interface(server['id'],
                                               port_id=port['id'])
        waiters.wait_for_interface_status(self.interface_client,
                                          server['id'], port['id'], 'ACTIVE')
        return self.wait_for_port_bound(port['id'])

    def detach_port(self, server, port):
        resp = self.interface_client.delete_interface(server['id'],
                                                      port['id'])
        # Instance action events are only shown to admins.
        waiters.wait_for_interface_detach(
            self.admin_servers_client, server['id'], port['id'],
            resp.response['x-openstack-request-id'])
        return self.wait_for_port_unbound(port['id'])

    def wait_in_guest(self, check, description):
        """Wait until check() returns a true value, and return it.

        check() runs commands in the guest, and is retried if they fail.
        """
        result = []

        def attempt():
            try:
                value = check()
            except lib_exc.SSHExecCommandFailed:
                return False
            if value:
                result.append(value)
            return bool(value)

        if not test_utils.call_until_true(
                attempt, CONF.network.build_timeout,
                CONF.network.build_interval):
            raise lib_exc.TimeoutException(
                'Guest did not %s within %s s' % (
                    description, CONF.network.build_timeout))
        return result[0]

    def wait_for_ipoib_interface(self, ssh, port):
        """Wait for the guest's IPoIB interface of a port, and return it.

        It is the interface with the GUID derived from the port's MAC
        address.
        """
        def find():
            for line in ssh.exec_command(LIST_IPOIB_INTERFACES).splitlines():
                name, _, address = line.strip().partition(' ')
                if guid_matches_mac(address, port['mac_address']):
                    return name

        return self.wait_in_guest(
            find, 'have an IPoIB interface for port %s with MAC address %s' %
            (port['id'], port['mac_address']))

    def get_ib_port_state(self, ssh, interface):
        """Return the state of the InfiniBand port of an IPoIB interface.

        It is read from /sys/class/infiniband/*/ports/1/state of the
        interface's device, which has a line such as "4: ACTIVE".
        """
        output = ssh.exec_command(
            'cat /sys/class/net/%s/device/infiniband/*/ports/1/state' %
            interface)
        return output.strip().partition(':')[2].strip()

    def wait_for_ib_port_state(self, ssh, interface, state):
        self.wait_in_guest(
            lambda: self.get_ib_port_state(ssh, interface) == state,
            'have InfiniBand port state %s on %s' % (state, interface))

    def configure_ipoib_interface(self, ssh, interface, port):
        """Give an IPoIB interface its port's fixed IP address.

        The InfiniBand subnets have no DHCP and no gateway.
        """
        prefixlen = ipaddress.ip_network(
            CONF.networking_mlnx.subnet_cidr).prefixlen
        address = port['fixed_ips'][0]['ip_address']
        # Keep NetworkManager from trying DHCP on the interface and
        # removing the address.
        ssh.exec_command(
            'if command -v nmcli >/dev/null; then '
            'sudo nmcli device set %(dev)s managed no; fi && '
            'sudo ip link set %(dev)s up && '
            'sudo ip addr add %(address)s/%(prefixlen)s dev %(dev)s' % {
                'dev': interface, 'address': address,
                'prefixlen': prefixlen})
        return address

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
                LIST_IPOIB_INTERFACES).split(),
            'remove %s' % interface2)
        self.attach_port(server2, port3)
        interface3 = self.wait_for_ipoib_interface(ssh2, port3)
        self.wait_for_ib_port_state(ssh2, interface3, 'ACTIVE')
        self.configure_ipoib_interface(ssh2, interface3, port3)
        self.check_remote_connectivity(ssh1, address2, nic=interface1)
