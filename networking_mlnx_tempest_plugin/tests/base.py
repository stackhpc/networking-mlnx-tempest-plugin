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

    def assert_port_bound(self, port, server):
        """Check that port is bound to a VF on the server's host."""
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
            self.assert_client_id(port)

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

    def assert_port_unbound(self, port):
        self.assertEqual('', port['device_id'])
        self.assertEqual('', port['binding:host_id'])
        self.assertNotIn('pci_slot', port['binding:profile'])
        if utils.is_extension_enabled('extra_dhcp_opt', 'network'):
            self.assert_no_client_id(port)
