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

# Point-to-point addresses for IPoIB child interfaces, from the benchmarking
# range (RFC 2544), outside any subnet the tests create
CHILD_ADDRESSES = ('198.18.0.1', '198.18.0.2')


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

    def skip_unless_pkeys_enforced(self, network):
        """Skip unless something keeps the network's ports in its pkey."""
        if not (self.is_sdn_synced(network['id']) or
                (CONF.networking_mlnx.allow_unenforced_pkeys and
                 CONF.networking_mlnx.network_type == 'vlan')):
            raise self.skipException(
                'Nothing is known to keep ports in their partition keys: '
                'mlnx_sdn_assist does not sync the InfiniBand network, and '
                '[networking_mlnx] allow_unenforced_pkeys is False or '
                'network_type is not vlan')

    def skip_unless_sdn_synced(self, network):
        """Skip unless an SDN controller keeps ports in their partitions.

        Without one, the subnet manager's partitions are the admin's, which
        typically make every port a member of every network's partition.
        """
        if not self.is_sdn_synced(network['id']):
            raise self.skipException(
                'mlnx_sdn_assist does not sync the InfiniBand network, so '
                'nothing is known to limit ports to their own partitions')

    @staticmethod
    def addresses(count):
        """Return addresses at the end of subnet_cidr, before the last."""
        subnet = ipaddress.ip_network(CONF.networking_mlnx.subnet_cidr)
        if subnet.num_addresses < count + 2:
            raise ValueError('[networking_mlnx] subnet_cidr is too small')
        return [str(subnet[-2 - i]) for i in range(count)]

    @staticmethod
    def full_pkey(network):
        """Return a VLAN network's pkey, with the full membership bit."""
        return 0x8000 | network['provider:segmentation_id']

    def create_guest_on(self, network, address):
        """Create a guest with an InfiniBand port on a network.

        The port's IPoIB interface has the port's fixed IP address, address.
        Returns the guest's SSH client and the interface.
        """
        # The port is created before the server, so that the server is
        # deleted first.
        port = self.create_infiniband_port(
            network, fixed_ips=[{'ip_address': address}])
        server, ssh = self.create_guest()
        self.attach_port(server, port)
        interface = self.wait_for_ipoib_interface(ssh, port)
        self.wait_for_ib_port_state(ssh, interface, 'ACTIVE')
        self.configure_ipoib_interface(ssh, interface, port)
        return ssh, interface

    @staticmethod
    def get_pkey_table(ssh, interface):
        """Return the pkey table of the guest's port, by index."""
        output = ssh.exec_command(
            'cd /sys/class/net/%s/device/infiniband/*/ports/1/pkeys && '
            'ls | sort -n | xargs cat' % interface)
        return [int(line, 16) for line in output.split()]

    def get_memberships(self, ssh, interface):
        """Return the pkeys whose partitions the guest's port is in."""
        return [pkey for pkey in self.get_pkey_table(ssh, interface)
                if pkey & 0x7fff]

    @staticmethod
    def get_interface_pkey(ssh, interface):
        """Return the pkey that an IPoIB interface uses."""
        return int(ssh.exec_command(
            'cat /sys/class/net/%s/pkey' % interface).strip(), 16)

    @staticmethod
    def has_carrier(ssh, interface):
        return ssh.exec_command(
            'cat /sys/class/net/%s/carrier 2>/dev/null || echo 0' %
            interface).strip() == '1'

    def add_pkey_interface(self, ssh, interface, pkey, address, peer):
        """Add an IPoIB child interface for a pkey, to reach one peer.

        A guest can do this for any pkey. The interface only has carrier
        if its port is a member of the partition. Its address is a
        point-to-point one, outside subnet_cidr, so that it only routes to
        its peer, whatever the guest's other interfaces are.
        """
        child = '%s.%04x' % (interface[:10], pkey)
        ssh.exec_command(
            'sudo ip link add link %s name %s type ipoib pkey 0x%04x' % (
                interface, child, pkey))
        # NetworkManager may not know the device straight away; until it
        # does, it can't be told to leave it alone.
        self.wait_in_guest(
            lambda: ssh.exec_command(
                'if ! command -v nmcli >/dev/null; then echo ok; '
                'elif sudo nmcli device set %s managed no >/dev/null 2>&1; '
                'then echo ok; fi' % child).strip() == 'ok',
            'have NetworkManager know %s' % child)
        ssh.exec_command(
            'sudo ip link set %(dev)s up && '
            'sudo ip addr add %(address)s peer %(peer)s dev %(dev)s' % {
                'dev': child, 'address': address, 'peer': peer})
        return child

    def wait_for_carrier(self, ssh, interface):
        self.wait_in_guest(lambda: self.has_carrier(ssh, interface),
                           'have carrier on %s' % interface)

    def assert_unreachable(self, ssh, interface, address):
        # Address resolution over IPoIB can take a while once the address
        # is added, so ping for longer than a single packet.
        self.assertRaises(
            lib_exc.SSHExecCommandFailed, ssh.exec_command,
            'sudo ping -I %s -c 10 -w 20 %s' % (interface, address))

    @decorators.idempotent_id('0b8d39c4-5f86-4c51-9a3e-2d2f7c9a6e13')
    def test_guests_on_one_network_communicate(self):
        """Check that guests on the same VLAN network reach each other.

        With an SDN controller, each guest's IPoIB interface must use its
        network's pkey, as a full member: the interface uses the pkey at
        index 0 of its port, where the controller must put the network's.
        Each guest pings the other. Both then add an IPoIB child interface
        for the network's pkey and ping each other over it, which shows
        that such interfaces work, as the tests that guests can't use
        other partitions' rely on.
        """
        network = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        self.skip_unless_pkeys_enforced(network)
        address1, address2 = self.addresses(2)
        ssh1, interface1 = self.create_guest_on(network, address1)
        ssh2, interface2 = self.create_guest_on(network, address2)

        synced = self.is_sdn_synced(network['id'])
        if synced:
            for ssh, interface in ((ssh1, interface1), (ssh2, interface2)):
                self.assertEqual(
                    self.full_pkey(network),
                    self.get_interface_pkey(ssh, interface),
                    "The guest's IPoIB interface doesn't use its network's "
                    "pkey: the SDN controller must put it at index 0 of the "
                    "port's pkey table (UFM's index0)")
        self.check_remote_connectivity(ssh1, address2, nic=interface1)
        self.check_remote_connectivity(ssh2, address1, nic=interface2)

        if synced:
            pkey = self.full_pkey(network)
            child1 = self.add_pkey_interface(
                ssh1, interface1, pkey, CHILD_ADDRESSES[0], CHILD_ADDRESSES[1])
            child2 = self.add_pkey_interface(
                ssh2, interface2, pkey, CHILD_ADDRESSES[1], CHILD_ADDRESSES[0])
            self.wait_for_carrier(ssh1, child1)
            self.wait_for_carrier(ssh2, child2)
            self.check_remote_connectivity(
                ssh1, CHILD_ADDRESSES[1], nic=child1)

    @decorators.idempotent_id('7c1a4e2b-93d8-4f60-b5a7-1e4c8d2f9b36')
    def test_guest_cant_use_another_networks_pkey(self):
        """Check that a guest can't reach another network with its pkey.

        A guest on one VLAN network adds an IPoIB child interface for the
        pkey of a second network, which another guest is on. Its port must
        not be a member of that partition, even a limited one, so the pkey
        isn't in its port's pkey table, the interface has no carrier, and
        it can't reach the other guest.
        """
        network_a = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        self.skip_unless_sdn_synced(network_a)
        network_b = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        address1, address2 = self.addresses(2)
        ssh1, interface1 = self.create_guest_on(network_a, address1)
        ssh2, interface2 = self.create_guest_on(network_b, address2)

        pkey_b = self.full_pkey(network_b)
        self.assertNotIn(pkey_b & 0x7fff, [
            pkey & 0x7fff for pkey in self.get_memberships(ssh1, interface1)])
        child = self.add_pkey_interface(
            ssh1, interface1, pkey_b, CHILD_ADDRESSES[0], address2)
        self.assertFalse(self.has_carrier(ssh1, child))
        self.assert_unreachable(ssh1, child, address2)

    @decorators.idempotent_id('d3e6f8a1-2b47-4c9e-8f05-6a1b7c3d9e42')
    def test_guests_cant_communicate_in_default_partition(self):
        """Check whether guests reach each other in the default partition.

        Guests on different VLAN networks each add an IPoIB child interface
        for the default partition. Their ports must be only limited members
        of it, 0x7fff, so the interfaces have carrier, but limited members
        can't reach each other. With [networking_mlnx]
        shared_default_partition, ports are full members, 0xffff, and the
        guests must reach each other instead.
        """
        network_a = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        self.skip_unless_sdn_synced(network_a)
        network_b = self.create_infiniband_network(
            self.addCleanup, network_type='vlan')
        address1, address2 = self.addresses(2)
        ssh1, interface1 = self.create_guest_on(network_a, address1)
        ssh2, interface2 = self.create_guest_on(network_b, address2)

        shared = CONF.networking_mlnx.shared_default_partition
        membership = 0xffff if shared else 0x7fff
        for ssh, interface in ((ssh1, interface1), (ssh2, interface2)):
            memberships = self.get_memberships(ssh, interface)
            self.assertIn(membership, memberships)
            self.assertNotIn(membership ^ 0x8000, memberships)
        child1 = self.add_pkey_interface(
            ssh1, interface1, 0xffff, CHILD_ADDRESSES[0], CHILD_ADDRESSES[1])
        child2 = self.add_pkey_interface(
            ssh2, interface2, 0xffff, CHILD_ADDRESSES[1], CHILD_ADDRESSES[0])
        self.wait_for_carrier(ssh1, child1)
        self.wait_for_carrier(ssh2, child2)
        if shared:
            self.check_remote_connectivity(
                ssh1, CHILD_ADDRESSES[1], nic=child1)
        else:
            self.assert_unreachable(ssh1, child1, CHILD_ADDRESSES[1])

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
        self.skip_unless_pkeys_enforced(network_a)
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
        self.assert_unreachable(ssh1, interface1, address2)

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
