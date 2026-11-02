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

from tempest.common import utils
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib.common.utils import test_utils
from tempest.lib import decorators
from tempest.lib import exceptions as lib_exc

from networking_mlnx_tempest_plugin.tests import base

CONF = config.CONF

NOT_SYNCED = 'mlnx_sdn_assist does not sync the InfiniBand network'
NO_VLAN_BINDING = (
    'mlnx_infiniband does not bind ports to VLAN networks here: '
    'mlnx_sdn_assist does not sync the InfiniBand network, and '
    '[networking_mlnx] allow_unenforced_pkeys is False')


# Each test creates VLAN networks on the InfiniBand physical network.
@decorators.serial
class PkeyTest(base.BaseInfinibandTest):
    """Check how networking-mlnx keeps ports in their partition key.

    The tests bind direct ports without servers, by setting
    binding:host_id as an admin to a host whose neutron-mlnx-agent maps
    physical_network, as Nova does when it boots a server. When
    mlnx_sdn_assist syncs the network, the SDN controller then adds the
    port's GUID to the network's partition key, although no virtual
    function has it, until the test deletes the port.
    """

    credentials = ['primary', 'admin']

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        if not utils.is_extension_enabled('agent', 'network'):
            raise cls.skipException('The agent extension is not enabled')
        cls.check_network_mtu()

    @classmethod
    def setup_clients(cls):
        super().setup_clients()
        cls.ports_client = cls.os_primary.ports_client
        cls.admin_segments_client = cls.os_admin.segments_client

    @classmethod
    def resource_setup(cls):
        super().resource_setup()
        hosts = cls.get_agent_hosts()
        if not hosts:
            raise cls.skipException(
                'No alive %s maps physical network %s' % (
                    base.AGENT_BINARY, CONF.networking_mlnx.physical_network))
        cls.host = hosts[0]

    def create_vlan_network(self, subnet=True):
        return self.create_infiniband_network(
            self.addCleanup, network_type='vlan', subnet=subnet)

    def create_direct_port(self, network, **extra):
        # Nova creates or updates the port with a compute device owner, for
        # which mlnx_infiniband sets the DHCP client ID.
        return self.create_infiniband_port(
            network, device_owner='compute:nova', **extra)

    def create_project_port(self, network, **kwargs):
        port = self.ports_client.create_port(
            network_id=network['id'],
            name=data_utils.rand_name(self.__class__.__name__ + '-port'),
            **kwargs)['port']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.ports_client.delete_port, port['id'])
        return port

    def create_segment(self, **kwargs):
        segment = self.admin_segments_client.create_segment(
            name=data_utils.rand_name(self.__class__.__name__ + '-segment'),
            **kwargs)['segment']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.admin_segments_client.delete_segment,
                        segment['id'])
        return segment

    def bind_port(self, port):
        """Bind a port to a host, as Nova does, and return it."""
        self.admin_ports_client.update_port(
            port['id'], **{'binding:host_id': self.host})
        return self.admin_ports_client.show_port(port['id'])['port']

    def assert_binding_refused(self, port, message):
        """Check that mlnx_sdn_assist refuses to bind a port.

        It refuses when ML2 commits the binding, so the update returns
        400 and the port stays unbound on the host.
        """
        exc = self.assertRaises(
            lib_exc.BadRequest, self.admin_ports_client.update_port,
            port['id'], **{'binding:host_id': self.host})
        self.assertIn(message, str(exc))
        port = self.admin_ports_client.show_port(port['id'])['port']
        self.assertEqual('unbound', port['binding:vif_type'])
        self.assertEqual(self.host, port['binding:host_id'])

    def skip_unless_client_id_restricted(self, network):
        # With sdn_sync = enabled, check that client IDs are restricted
        # rather than detect it.
        if not (self.is_sdn_synced(network['id']) or
                self.is_client_id_restricted(network['id'])):
            raise self.skipException(
                'mlnx_sdn_assist does not restrict client IDs on the '
                'InfiniBand network')

    def assert_port_bound(self, port):
        self.assertEqual(base.VIF_TYPE, port['binding:vif_type'])
        self.assertEqual(self.host, port['binding:host_id'])
        self.assertEqual(
            CONF.networking_mlnx.physical_network,
            port['binding:vif_details'].get('physical_network'))

    @decorators.idempotent_id('811ce412-f7fe-476a-a214-77f8425d43cb')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_project_cannot_set_client_id(self):
        """Check that a project can't set a port's DHCP client ID.

        The SDN controller adds the GUID of a port's client ID to its
        network's partition key, so a project that could set it could put
        another port into its network. Unless [networking_mlnx] sdn_sync
        is enabled, whether client IDs are restricted is detected by
        creating a port with one, so the test then only checks the update
        and the numeric option name.
        """
        network = self.create_vlan_network()
        self.skip_unless_client_id_restricted(network)

        exc = self.assertRaises(
            lib_exc.BadRequest, self.create_project_port, network,
            extra_dhcp_opts=[{'opt_name': 'client-id',
                              'opt_value': 'tempest-client-id',
                              'ip_version': 4}])
        self.assertTrue(base.is_client_id_refusal(exc), str(exc))
        port = self.create_project_port(network)
        exc = self.assertRaises(
            lib_exc.BadRequest, self.ports_client.update_port, port['id'],
            extra_dhcp_opts=[{'opt_name': '61',
                              'opt_value': 'tempest-client-id',
                              'ip_version': 4}])
        self.assertTrue(base.is_client_id_refusal(exc), str(exc))

    @decorators.idempotent_id('f170b7d9-bcb0-45a6-ba01-fde0cee1d999')
    def test_vlan_port_not_bound_without_pkey_enforcement(self):
        """Check that a VLAN port isn't bound if nothing enforces its pkey.

        Without an SDN controller that adds the port to its network's
        partition key, a virtual function on a VLAN network would be in no
        partition or the wrong one, so mlnx_infiniband doesn't bind it.
        """
        network = self.create_vlan_network()
        if self.is_vlan_binding_allowed(network['id']):
            raise self.skipException(
                'mlnx_infiniband binds ports to VLAN networks here')

        port = self.bind_port(self.create_direct_port(network))
        self.assertEqual('binding_failed', port['binding:vif_type'])

    @decorators.idempotent_id('94a3e66f-b2b1-443d-9dd8-177f14c19c7a')
    def test_vlan_port_bound_with_pkey_enforcement(self):
        """Check that a VLAN port is bound when its pkey is enforced.

        That is when mlnx_sdn_assist syncs the network, or the Neutron
        server allows unenforced partition keys.
        """
        network = self.create_vlan_network()
        if not self.is_vlan_binding_allowed(network['id']):
            raise self.skipException(NO_VLAN_BINDING)

        port = self.bind_port(self.create_direct_port(network))
        self.assert_port_bound(port)

    @decorators.idempotent_id('9c195f3b-4894-4808-844b-a9d30320c6e4')
    def test_port_with_same_mac_not_bound(self):
        """Check that a port with a bound port's MAC address isn't bound.

        A virtual function's GUID is derived from its port's MAC address,
        which is only unique within a network. A port on another network
        with the same MAC address would put the same GUID in both
        networks' partition keys.
        """
        network1 = self.create_vlan_network()
        if not self.is_vlan_binding_allowed(network1['id']):
            raise self.skipException(NO_VLAN_BINDING)
        network2 = self.create_vlan_network()
        port1 = self.bind_port(self.create_direct_port(network1))
        self.assert_port_bound(port1)

        port2 = self.create_direct_port(
            network2, mac_address=port1['mac_address'])
        self.assertEqual(port1['mac_address'], port2['mac_address'])
        port2 = self.bind_port(port2)
        self.assertEqual('binding_failed', port2['binding:vif_type'])

    @decorators.idempotent_id('e54d3dbe-38c3-43e7-b915-afd602d599b5')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_direct_port_with_other_client_id_not_bound(self):
        """Check that a direct port with another client ID isn't bound.

        The SDN controller adds the GUID of the port's client ID to the
        partition key, but the virtual function's GUID is derived from
        the port's MAC address, so mlnx_sdn_assist doesn't bind a direct
        port whose client ID isn't the one derived from it, even if an
        admin set it.
        """
        network = self.create_vlan_network()
        self.skip_unless_client_id_restricted(network)

        port = self.create_direct_port(network, extra_dhcp_opts=[
            {'opt_name': 'client-id', 'opt_value': 'tempest-client-id',
             'ip_version': 4}])
        self.assert_binding_refused(port, "doesn't match its MAC address")

    @decorators.idempotent_id('3c9864dd-bad6-4288-96bc-e18042c57264')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_port_with_two_client_ids_not_bound(self):
        """Check that a port with two client IDs isn't bound.

        Which of them the SDN controller would use is undefined.
        """
        network = self.create_vlan_network()
        self.skip_unless_client_id_restricted(network)

        port = self.create_direct_port(network, extra_dhcp_opts=[
            {'opt_name': 'client-id', 'opt_value': 'tempest-client-id-1',
             'ip_version': 4},
            {'opt_name': '61', 'opt_value': 'tempest-client-id-2',
             'ip_version': 4}])
        self.assert_binding_refused(port, 'more than one DHCP client ID')

    @decorators.idempotent_id('5a6b7d32-9371-40c4-a0b1-6c64d6e84bcd')
    @utils.requires_ext(extension='segment', service='network')
    def test_segment_on_unsynced_physical_network_refused(self):
        """Check that a synced network can't get an unsynced segment.

        mlnx_sdn_assist only syncs a network while all its segments are
        on physical networks that it syncs, so a segment on another one
        would stop the network's ports, including their deletion, from
        being sent to the SDN controller.
        """
        unsynced_physnet = CONF.networking_mlnx.unsynced_physical_network
        if not unsynced_physnet:
            raise self.skipException(
                '[networking_mlnx] unsynced_physical_network is not set')
        network = self.create_vlan_network(subnet=False)
        if not self.is_sdn_synced(network['id']):
            raise self.skipException(NOT_SYNCED)

        exc = self.assertRaises(
            lib_exc.BadRequest, self.create_segment,
            network_id=network['id'], network_type='vlan',
            physical_network=unsynced_physnet)
        self.assertIn('synced to the SDN controller', str(exc))
