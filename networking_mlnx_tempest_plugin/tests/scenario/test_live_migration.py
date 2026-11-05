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
from tempest.lib.common import api_version_request
from tempest.lib import decorators

from networking_mlnx_tempest_plugin.tests import base

CONF = config.CONF


# Neutron allows only one flat network on each physical network, and each
# test uses InfiniBand virtual functions, of which hosts have only a few.
@decorators.serial
class InfinibandLiveMigrationTest(base.BaseInfinibandGuestTest):
    """Live-migrate guests with InfiniBand ports.

    Nova hot-unplugs a direct port's virtual function from the guest
    before the migration and hot-plugs one from the destination host
    after it. Nova must not wait for the network-vif-plugged event of the
    InfiniBand port, with [compute] live_migration_wait_for_vif_plug =
    False on all compute hosts, which the test can't detect, so it is
    skipped unless [networking_mlnx] live_migration is True.
    """

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        if not CONF.networking_mlnx.live_migration:
            raise cls.skipException(
                '[networking_mlnx] live_migration is False')
        if not CONF.compute_feature_enabled.live_migration:
            raise cls.skipException('Live migration is not available')
        # Nova only live-migrates servers with SR-IOV ports if Neutron has
        # multiple port bindings.
        if not utils.is_extension_enabled('binding-extended', 'network'):
            raise cls.skipException(
                'The binding-extended extension is not enabled')

    @classmethod
    def resource_setup(cls):
        super().resource_setup()
        cls.migration_hosts = cls.get_migration_hosts()
        if len(cls.migration_hosts) < 2:
            raise cls.skipException(
                'Live migration needs two enabled and up compute hosts with '
                'an alive neutron-mlnx-agent that maps physical network %s, '
                'under the same host name, but only these hosts have both: '
                '%s' % (
                    CONF.networking_mlnx.physical_network,
                    ', '.join(cls.migration_hosts) or 'none'))

    @classmethod
    def get_migration_hosts(cls):
        """Return the hosts that guests with InfiniBand ports can move to.

        Those are the hosts of enabled and up nova-compute services that
        have an alive neutron-mlnx-agent that maps physical_network.
        """
        agent_hosts = set(cls.get_agent_hosts())
        services = cls.os_admin.services_client.list_services(
            binary='nova-compute')['services']
        return sorted(service['host'] for service in services
                      if service['state'] == 'up' and
                      service['status'] == 'enabled' and
                      service['host'] in agent_hosts)

    def live_migrate(self, server, host):
        """Live-migrate a server to a host and wait for it to be ACTIVE.

        Block migration is used if [compute-feature-enabled]
        block_migration_for_live_migration is True, as in Tempest's own
        live migration tests.
        """
        kwargs = {
            'host': host,
            'block_migration': (CONF.compute_feature_enabled.
                                block_migration_for_live_migration),
        }
        # disk_over_commit is required before microversion 2.25 and not
        # allowed from it.
        microversion = self.compute_request_microversion
        if (microversion is None or
                api_version_request.APIVersionRequest(microversion) <
                api_version_request.APIVersionRequest('2.25')):
            kwargs['disk_over_commit'] = False
        self.admin_servers_client.live_migrate_server(server['id'], **kwargs)
        waiters.wait_for_server_status(self.servers_client, server['id'],
                                       'ACTIVE')
        actual_host = self.get_host_for_server(server['id'])
        if actual_host != host:
            migrations = [
                migration for migration in
                self.os_admin.migrations_client.list_migrations()[
                    'migrations']
                if migration['instance_uuid'] == server['id']]
            self.fail('Server %s is on host %s rather than %s after live '
                      'migration. Its migrations: %s' % (
                          server['id'], actual_host, host, migrations))

    def get_port_guid(self, ssh, interface):
        """Return the port GUID of an IPoIB interface.

        It is the last 8 bytes of the interface's hardware address.
        """
        address = ssh.exec_command('cat /sys/class/net/%s/address' %
                                   interface)
        return ':'.join(address.strip().lower().split(':')[-8:])

    @decorators.attr(type='multinode')
    @decorators.idempotent_id('6107bb73-9354-462a-9756-7c558c672c88')
    def test_live_migrate_server_with_infiniband_port(self):
        """Live-migrate a guest with an InfiniBand port to another host.

        A guest and a peer guest each have a port on the configured
        InfiniBand network, with an IPoIB address, and can ping each
        other. The guest is live-migrated to another host with a
        neutron-mlnx-agent. Its port must then be bound to a virtual
        function on that host, which may be at another PCI address.

        The guest gets a new virtual function, whose InfiniBand port must
        become ACTIVE with the same GUID as before, since the GUID is
        derived from the port's MAC address. Its IPoIB interface is then
        configured again, and the guest must reach the peer again. The
        peer stays where the scheduler placed it, which may be the
        destination host.
        """
        network = self.get_infiniband_network(self.addCleanup)
        if (network['provider:network_type'] == 'vlan' and
                not self.is_vlan_binding_allowed(network['id'])):
            raise self.skipException(
                'mlnx_infiniband does not bind ports to VLAN networks here: '
                'mlnx_sdn_assist does not sync the InfiniBand network, and '
                '[networking_mlnx] allow_unenforced_pkeys is False')
        # Create the ports before the servers, so that the servers are
        # deleted first.
        port = self.create_infiniband_port(network)
        peer_port = self.create_infiniband_port(network)
        server, ssh = self.create_guest()
        peer, peer_ssh = self.create_guest()

        port = self.attach_port(server, port)
        self.attach_port(peer, peer_port)
        interface = self.wait_for_ipoib_interface(ssh, port)
        peer_interface = self.wait_for_ipoib_interface(peer_ssh, peer_port)
        self.wait_for_ib_port_state(ssh, interface, 'ACTIVE')
        self.wait_for_ib_port_state(peer_ssh, peer_interface, 'ACTIVE')
        self.configure_ipoib_interface(ssh, interface, port)
        peer_address = self.configure_ipoib_interface(
            peer_ssh, peer_interface, peer_port)
        guid = self.get_port_guid(ssh, interface)
        self.check_remote_connectivity(ssh, peer_address, nic=interface)

        source_host = self.get_host_for_server(server['id'])
        source_pci_slot = port['binding:profile'].get('pci_slot')
        destination_host = next(
            host for host in self.migration_hosts if host != source_host)
        self.live_migrate(server, destination_host)

        port = self.wait_for_port(
            port['id'],
            lambda p: (p['binding:host_id'] == destination_host and
                       p['status'] == 'ACTIVE' and
                       p['binding:vif_type'] == base.VIF_TYPE),
            'ACTIVE and bound to %s' % destination_host)
        self.assertEqual(server['id'], port['device_id'])
        self.assertEqual(base.VNIC_TYPE, port['binding:vnic_type'])
        # The virtual function on the destination host may be at another
        # PCI address than the one on the source host.
        self.assertIn('pci_slot', port['binding:profile'],
                      'Port %s has no PCI slot on %s; it had %s on %s' % (
                          port['id'], destination_host, source_pci_slot,
                          source_host))

        interface = self.wait_for_ipoib_interface(ssh, port)
        self.wait_for_ib_port_state(ssh, interface, 'ACTIVE')
        self.assertEqual(guid, self.get_port_guid(ssh, interface))
        self.configure_ipoib_interface(ssh, interface, port)
        self.check_remote_connectivity(ssh, peer_address, nic=interface)
