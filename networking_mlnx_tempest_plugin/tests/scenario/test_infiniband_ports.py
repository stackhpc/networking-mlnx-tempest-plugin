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
from tempest.lib import decorators

from networking_mlnx_tempest_plugin.tests import base


# Neutron allows only one flat network on each physical network, and each
# test uses InfiniBand virtual functions, of which hosts have only a few.
@decorators.serial
class InfinibandPortTest(base.BaseInfinibandPortTest):

    @decorators.idempotent_id('6baa299b-d8cb-4c1e-9fbd-ac7a4e5a05ea')
    def test_boot_server_with_infiniband_port(self):
        """Boot a server with an InfiniBand port, then delete it.

        The port is bound to a virtual function on the server's host while
        the server exists, and released when it is deleted.
        """
        port = self.create_infiniband_port()
        server = self.create_server([port])

        port = self.wait_for_port_bound(port['id'])
        self.assert_port_bound(port, server)

        self.delete_server(server)
        port = self.wait_for_port_unbound(port['id'])
        self.assert_port_unbound(port)

    @decorators.idempotent_id('ac92df01-d192-4faf-a5ee-673ca6bdbe4e')
    def test_attach_detach_infiniband_port(self):
        """Attach an InfiniBand port to a running server, then detach it."""
        server = self.create_server([self.create_infiniband_port()])
        port = self.create_infiniband_port()

        self.interfaces_client.create_interface(server['id'],
                                                port_id=port['id'])
        waiters.wait_for_interface_status(self.interfaces_client,
                                          server['id'], port['id'], 'ACTIVE')
        port = self.wait_for_port_bound(port['id'])
        self.assert_port_bound(port, server)

        resp = self.interfaces_client.delete_interface(server['id'],
                                                       port['id'])
        # Instance action events are only shown to admins.
        waiters.wait_for_interface_detach(
            self.admin_servers_client, server['id'], port['id'],
            resp.response['x-openstack-request-id'])
        port = self.wait_for_port_unbound(port['id'])
        self.assert_port_unbound(port)

    @decorators.idempotent_id('83ffabed-d462-4d73-a386-d059384b4c5d')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_unbind_keeps_other_dhcp_options(self):
        """Check that unbinding a port only removes the client ID.

        The mechanism driver removes the client-id DHCP option it added
        when the port is unbound, and must leave the port's other options.
        """
        tftp_opt = {'opt_name': 'tftp-server', 'opt_value': '192.0.2.1',
                    'ip_version': 4}
        port = self.create_infiniband_port(extra_dhcp_opts=[tftp_opt])
        server = self.create_server([port])

        port = self.wait_for_port_bound(port['id'])
        self.assert_port_bound(port, server)
        self.assertIn(tftp_opt, port['extra_dhcp_opts'])

        self.delete_server(server)
        port = self.wait_for_port_unbound(port['id'])
        self.assert_port_unbound(port)
        self.assertEqual([tftp_opt], port['extra_dhcp_opts'])

    @decorators.idempotent_id('82ba2c91-6f30-4a3b-a278-092024c230a0')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_client_id_only_on_infiniband_ports(self):
        """Check that only the server's InfiniBand port gets a client ID.

        ML2 calls the mechanism driver for every port, and the driver must
        not add its client-id option to ports it can't bind, such as the
        server's port on a project network.
        """
        project_port = self.create_project_port()
        infiniband_port = self.create_infiniband_port()
        server = self.create_server([infiniband_port, project_port])

        infiniband_port = self.wait_for_port_bound(infiniband_port['id'])
        self.assert_port_bound(infiniband_port, server)
        project_port = self.wait_for_port(
            project_port['id'], lambda p: p['status'] == 'ACTIVE', 'ACTIVE')
        self.assertNotEqual(base.VIF_TYPE, project_port['binding:vif_type'])
        self.assert_no_client_id(project_port)

    @decorators.idempotent_id('3bd33b98-1017-4b50-948c-4abce9c9abae')
    @utils.requires_ext(extension='extra_dhcp_opt', service='network')
    def test_keep_user_client_id(self):
        """Check that a client ID set by the user is kept.

        The mechanism driver must not replace a client-id option set on a
        port it doesn't bind, and on unbind it must only remove a client-id
        with the value it generates.
        """
        client_id_opt = {'opt_name': 'client-id',
                         'opt_value': 'tempest-client-id', 'ip_version': 4}
        port = self.create_project_port(extra_dhcp_opts=[client_id_opt])
        server = self.create_server([port])

        port = self.wait_for_port(
            port['id'], lambda p: p['status'] == 'ACTIVE', 'ACTIVE')
        self.assertEqual([client_id_opt], port['extra_dhcp_opts'])

        self.delete_server(server)
        port = self.wait_for_port(
            port['id'], lambda p: p['binding:vif_type'] == 'unbound',
            'unbound')
        self.assertEqual([client_id_opt], port['extra_dhcp_opts'])
