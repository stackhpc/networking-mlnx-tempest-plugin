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
from tempest.common import waiters
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib.common.utils import test_utils
from tempest.lib import exceptions as lib_exc
from tempest import test

CONF = config.CONF

AGENT_TYPE = 'Mellanox plugin agent'
AGENT_BINARY = 'neutron-mlnx-agent'
VIF_TYPE = 'ib_hostdev'
VNIC_TYPE = 'direct'
# The mechanism driver's client-id prefixes, used when its
# [mlnx] client_id_hardware option is True and False respectively.
CLIENT_ID_PREFIXES = ('20:', 'ff:00:00:00:00:00:02:00:00:02:c9:00:')


def is_client_id_refusal(exc):
    """Check if a BadRequest is mlnx_sdn_assist refusing a client ID."""
    return 'client id' in str(exc).lower()


class BaseInfinibandTest(test.BaseTestCase):
    """Base class for tests that need networking-mlnx."""

    credentials = ['admin']

    @classmethod
    def setup_credentials(cls):
        # The tests don't use the project networks that dynamic credentials
        # would otherwise create.
        cls.set_network_resources()
        super().setup_credentials()

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        if not CONF.service_available.neutron:
            raise cls.skipException('Neutron is not available')
        if not CONF.service_available.networking_mlnx:
            raise cls.skipException('networking-mlnx is not available')
        if not CONF.networking_mlnx.physical_network:
            raise cls.skipException(
                '[networking_mlnx] physical_network is not set')
        if not utils.is_extension_enabled('binding', 'network'):
            raise cls.skipException('The binding extension is not enabled')

    @classmethod
    def setup_clients(cls):
        super().setup_clients()
        cls.admin_agents_client = cls.os_admin.network_agents_client


class BaseInfinibandPortTest(BaseInfinibandTest):
    """Base class for tests that boot servers with InfiniBand ports.

    The admin client creates the InfiniBand network and ports on behalf of
    the primary project, which boots the servers. Only an admin can create
    provider networks and see port bindings.
    """

    credentials = ['primary', 'admin']

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        if not CONF.service_available.nova:
            raise cls.skipException('Nova is not available')
        if (CONF.networking_mlnx.network_mtu and
                not CONF.networking_mlnx.network_id and
                not utils.is_extension_enabled('net-mtu-writable',
                                               'network')):
            raise cls.skipException(
                '[networking_mlnx] network_mtu is set, but the '
                'net-mtu-writable extension is not enabled')

    @classmethod
    def setup_clients(cls):
        super().setup_clients()
        cls.admin_networks_client = cls.os_admin.networks_client
        cls.admin_subnets_client = cls.os_admin.subnets_client
        cls.admin_ports_client = cls.os_admin.ports_client
        cls.admin_servers_client = cls.os_admin.servers_client
        cls.servers_client = cls.os_primary.servers_client
        cls.networks_client = cls.os_primary.networks_client
        cls.subnets_client = cls.os_primary.subnets_client
        cls.ports_client = cls.os_primary.ports_client
        cls.interfaces_client = cls.os_primary.interfaces_client

    @classmethod
    def resource_setup(cls):
        super().resource_setup()
        if CONF.networking_mlnx.network_id:
            try:
                cls.network = cls.admin_networks_client.show_network(
                    CONF.networking_mlnx.network_id)['network']
            except lib_exc.NotFound:
                raise lib_exc.InvalidConfiguration(
                    'Network %s from [networking_mlnx] network_id does not '
                    'exist' % CONF.networking_mlnx.network_id)
            # Nova looks up the network with the primary project's
            # credentials, so it must be able to see it.
            if not cls.network['shared']:
                raise lib_exc.InvalidConfiguration(
                    'Network %s from [networking_mlnx] network_id is not '
                    'shared' % cls.network['id'])
            physnet = cls.network.get('provider:physical_network')
            if physnet != CONF.networking_mlnx.physical_network:
                raise lib_exc.InvalidConfiguration(
                    'Network %s from [networking_mlnx] network_id is on '
                    'physical network %s, not %s' % (
                        cls.network['id'], physnet,
                        CONF.networking_mlnx.physical_network))
        else:
            cls.network = cls._create_infiniband_network()
        if (cls.network.get('provider:network_type') == 'vlan' and
                not cls.is_vlan_binding_allowed(cls.network['id'])):
            raise cls.skipException(
                'mlnx_infiniband does not bind ports to VLAN networks here: '
                'mlnx_sdn_assist does not sync the InfiniBand network, and '
                '[networking_mlnx] allow_unenforced_pkeys is False')

    @classmethod
    def _create_infiniband_network(cls):
        mlnx_conf = CONF.networking_mlnx
        kwargs = {
            'name': data_utils.rand_name(cls.__name__ + '-network'),
            'project_id': cls.os_primary.credentials.project_id,
            'provider:network_type': mlnx_conf.network_type,
            'provider:physical_network': mlnx_conf.physical_network,
        }
        if (mlnx_conf.network_type == 'vlan' and
                mlnx_conf.segmentation_id is not None):
            kwargs['provider:segmentation_id'] = mlnx_conf.segmentation_id
        if mlnx_conf.network_mtu:
            kwargs['mtu'] = mlnx_conf.network_mtu
        # The mechanism driver can't enforce security groups, so the
        # InfiniBand networks and ports don't use port security.
        if utils.is_extension_enabled('port-security', 'network'):
            kwargs['port_security_enabled'] = False
        network = cls.admin_networks_client.create_network(**kwargs)['network']
        cls.addClassResourceCleanup(
            test_utils.call_and_ignore_notfound_exc,
            cls.admin_networks_client.delete_network, network['id'])
        # The guest has no DHCP client for IPoIB, so addresses are only
        # allocated by Neutron.
        subnet = cls.admin_subnets_client.create_subnet(
            network_id=network['id'],
            name=data_utils.rand_name(cls.__name__ + '-subnet'),
            project_id=cls.os_primary.credentials.project_id,
            cidr=mlnx_conf.subnet_cidr,
            ip_version=4,
            enable_dhcp=False,
            gateway_ip=None)['subnet']
        cls.addClassResourceCleanup(
            test_utils.call_and_ignore_notfound_exc,
            cls.admin_subnets_client.delete_subnet, subnet['id'])
        return network

    @classmethod
    def is_client_id_restricted(cls, network_id):
        """Check if mlnx_sdn_assist restricts client IDs on the network.

        It doesn't let projects set a DHCP client ID on a port of a
        network whose physical networks are in its [sdn]
        physical_networks, nor binds a direct port there whose client ID
        isn't the one derived from its MAC address, whether or not [sdn]
        sync_enabled is True. This is detected by trying to create a port
        with a client ID as the primary project. The result is cached for
        the test class, not inherited by its subclasses.

        :param network_id: an InfiniBand network on physical_network,
            on which the primary project can create ports.
        """
        if '_client_id_restricted' not in cls.__dict__:
            cls._client_id_restricted = cls._probe_client_id(network_id)
        return cls._client_id_restricted

    @classmethod
    def _probe_client_id(cls, network_id):
        if not utils.is_extension_enabled('extra_dhcp_opt', 'network'):
            return False
        ports_client = cls.os_primary.ports_client
        try:
            port = ports_client.create_port(
                network_id=network_id,
                name=data_utils.rand_name(cls.__name__ + '-client-id-probe'),
                extra_dhcp_opts=[{'opt_name': 'client-id',
                                  'opt_value': 'tempest-client-id-probe',
                                  'ip_version': 4}])['port']
        except lib_exc.BadRequest as exc:
            if is_client_id_refusal(exc):
                return True
            raise
        test_utils.call_and_ignore_notfound_exc(ports_client.delete_port,
                                                port['id'])
        return False

    @classmethod
    def is_sdn_synced(cls, network_id):
        """Check if mlnx_sdn_assist syncs the network to an SDN controller.

        If it does, the controller adds the network's ports to its
        partition key. Unless [networking_mlnx] sdn_sync says so, this is
        detected with is_client_id_restricted().
        """
        sdn_sync = CONF.networking_mlnx.sdn_sync
        if sdn_sync != 'auto':
            return sdn_sync == 'enabled'
        return cls.is_client_id_restricted(network_id)

    @classmethod
    def is_vlan_binding_allowed(cls, network_id):
        """Check if mlnx_infiniband binds ports to VLAN networks.

        It only does if mlnx_sdn_assist syncs the network, so that the SDN
        controller adds its ports to the network's partition key, or if
        the Neutron server allows unenforced partition keys.
        """
        return (CONF.networking_mlnx.allow_unenforced_pkeys or
                cls.is_sdn_synced(network_id))

    def create_infiniband_port(self, **extra):
        kwargs = {
            'network_id': self.network['id'],
            'name': data_utils.rand_name(self.__class__.__name__ + '-port'),
            'project_id': self.os_primary.credentials.project_id,
            'binding:vnic_type': VNIC_TYPE,
        }
        if utils.is_extension_enabled('port-security', 'network'):
            kwargs['port_security_enabled'] = False
        kwargs.update(extra)
        port = self.admin_ports_client.create_port(**kwargs)['port']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.admin_ports_client.delete_port, port['id'])
        return port

    def create_project_port(self, **kwargs):
        """Create a port on a new project network.

        The network has the default project network type, so the test is
        skipped if that puts it on a physical network that a Mellanox
        agent maps, where the mechanism driver could bind its ports.
        """
        name = self.__class__.__name__
        network = self.networks_client.create_network(
            name=data_utils.rand_name(name + '-project-network'))['network']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.networks_client.delete_network, network['id'])
        physnet = self.admin_networks_client.show_network(
            network['id'])['network'].get('provider:physical_network')
        agents = self.admin_agents_client.list_agents(
            agent_type=AGENT_TYPE)['agents']
        mlnx_physnets = {physnet for agent in agents
                         for physnet in agent['configurations'].get(
                             'interface_mappings', {})}
        if physnet in mlnx_physnets:
            raise self.skipException(
                'Project networks are on physical network %s, which a '
                'Mellanox agent maps' % physnet)
        subnet = self.subnets_client.create_subnet(
            network_id=network['id'],
            name=data_utils.rand_name(name + '-project-subnet'),
            cidr=CONF.network.project_network_cidr,
            ip_version=4,
            enable_dhcp=False)['subnet']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.subnets_client.delete_subnet, subnet['id'])
        port = self.ports_client.create_port(
            network_id=network['id'],
            name=data_utils.rand_name(name + '-project-port'),
            **kwargs)['port']
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.ports_client.delete_port, port['id'])
        return port

    def create_server(self, ports):
        server = self.servers_client.create_server(
            name=data_utils.rand_name(self.__class__.__name__ + '-server'),
            imageRef=CONF.compute.image_ref,
            flavorRef=CONF.compute.flavor_ref,
            networks=[{'port': port['id']} for port in ports])['server']
        self.addCleanup(waiters.wait_for_server_termination,
                        self.servers_client, server['id'])
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.servers_client.delete_server, server['id'])
        waiters.wait_for_server_status(self.servers_client, server['id'],
                                       'ACTIVE')
        return self.admin_servers_client.show_server(server['id'])['server']

    def delete_server(self, server):
        self.servers_client.delete_server(server['id'])
        waiters.wait_for_server_termination(self.servers_client,
                                            server['id'])

    def wait_for_port(self, port_id, predicate, description):
        """Wait until predicate(port) is true and return the port."""
        port = {}

        def check():
            port.update(
                self.admin_ports_client.show_port(port_id)['port'])
            return predicate(port)

        if not test_utils.call_until_true(
                check, CONF.network.build_timeout,
                CONF.network.build_interval):
            raise lib_exc.TimeoutException(
                'Port %s did not become %s within %s s. Last state: '
                'status %s, binding:vif_type %s, binding:host_id %s' % (
                    port_id, description, CONF.network.build_timeout,
                    port.get('status'), port.get('binding:vif_type'),
                    port.get('binding:host_id')))
        return port

    def wait_for_port_bound(self, port_id):
        return self.wait_for_port(
            port_id,
            lambda p: (p['status'] == 'ACTIVE' and
                       p['binding:vif_type'] == VIF_TYPE),
            'ACTIVE and bound')

    def wait_for_port_unbound(self, port_id):
        return self.wait_for_port(
            port_id,
            lambda p: (p['status'] == 'DOWN' and
                       p['binding:vif_type'] == 'unbound'),
            'DOWN and unbound')

    def assert_port_bound(self, port, server, user_client_id=None):
        """Check that port is bound to a VF on the server's host.

        If user_client_id is given, the port must have that client-id
        rather than the one the mechanism driver generates.
        """
        physnet = CONF.networking_mlnx.physical_network
        self.assertEqual(server['id'], port['device_id'])
        self.assertEqual(VIF_TYPE, port['binding:vif_type'])
        self.assertEqual(VNIC_TYPE, port['binding:vnic_type'])
        self.assertEqual(server['OS-EXT-SRV-ATTR:host'],
                         port['binding:host_id'])
        self.assertEqual(
            physnet, port['binding:vif_details'].get('physical_network'))
        profile = port['binding:profile']
        self.assertIn('pci_slot', profile)
        self.assertEqual(physnet, profile.get('physical_network'))
        if utils.is_extension_enabled('extra_dhcp_opt', 'network'):
            if user_client_id is None:
                self.assert_client_id(port)
            else:
                self.assert_user_client_id(port, user_client_id)

    def assert_user_client_id(self, port, client_id):
        """Check that the port has client_id as its only client ID."""
        client_ids = [opt['opt_value'] for opt in port['extra_dhcp_opts']
                      if opt['opt_name'] == 'client-id']
        self.assertEqual([client_id], client_ids, port['extra_dhcp_opts'])

    def assert_client_id(self, port):
        """Check the DHCP client ID that the mechanism driver sets.

        IPoIB clients identify themselves by a client ID rather than by
        hardware address, so the mechanism driver derives one from the
        port's MAC address when the port is bound to an instance.
        """
        client_ids = [opt['opt_value'] for opt in port['extra_dhcp_opts']
                      if opt['opt_name'] == 'client-id']
        self.assertEqual(1, len(client_ids), port['extra_dhcp_opts'])
        mac = port['mac_address']
        expected = [prefix + mac[:8] + ':00:00:' + mac[9:]
                    for prefix in CLIENT_ID_PREFIXES]
        self.assertIn(client_ids[0], expected,
                      'client-id does not match MAC address %s' % mac)

    def assert_no_client_id(self, port):
        self.assertNotIn('client-id',
                         [opt['opt_name'] for opt in port['extra_dhcp_opts']],
                         port['extra_dhcp_opts'])

    def assert_port_unbound(self, port, user_client_id=None):
        self.assertEqual('', port['device_id'])
        self.assertEqual('', port['binding:host_id'])
        self.assertNotIn('pci_slot', port['binding:profile'])
        if utils.is_extension_enabled('extra_dhcp_opt', 'network'):
            if user_client_id is None:
                self.assert_no_client_id(port)
            else:
                self.assert_user_client_id(port, user_client_id)
