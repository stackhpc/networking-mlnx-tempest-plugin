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
from tempest.lib import decorators

from networking_mlnx_tempest_plugin.tests import base

CONF = config.CONF


class MlnxAgentTest(base.BaseInfinibandTest):

    @classmethod
    def skip_checks(cls):
        super().skip_checks()
        if not utils.is_extension_enabled('agent', 'network'):
            raise cls.skipException('The agent extension is not enabled')

    @decorators.idempotent_id('fcaac29f-fec7-4722-8aab-2b9bad5f102d')
    def test_agent_maps_physical_network(self):
        """Check that an alive agent maps the InfiniBand physical network."""
        physnet = CONF.networking_mlnx.physical_network
        agents = self.admin_agents_client.list_agents(
            agent_type=base.AGENT_TYPE)['agents']
        self.assertNotEmpty(agents, 'No %s is registered' % base.AGENT_TYPE)
        for agent in agents:
            self.assertEqual(base.AGENT_BINARY, agent['binary'])
        # Agents of decommissioned hosts stay registered until an admin
        # deletes them, so only alive agents count.
        mapped = [agent['host'] for agent in agents
                  if agent['alive'] and physnet in agent['configurations'].get(
                      'interface_mappings', {})]
        self.assertNotEmpty(
            mapped, 'No alive agent maps physical network %s' % physnet)
