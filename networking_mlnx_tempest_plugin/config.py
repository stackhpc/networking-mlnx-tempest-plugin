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

from oslo_config import cfg

service_option = cfg.BoolOpt(
    'networking_mlnx',
    default=False,
    help='Whether networking-mlnx and its mlnx_infiniband mechanism driver '
         'are deployed.')

networking_mlnx_group = cfg.OptGroup(
    name='networking_mlnx',
    title='networking-mlnx test options')

NetworkingMlnxGroup = [
    cfg.StrOpt('physical_network',
               help='Physical network that is mapped to an InfiniBand '
                    'interface in the interface_mappings of '
                    'neutron-mlnx-agent. Tests that need it are skipped '
                    'when it is not set.'),
    cfg.StrOpt('network_type',
               default='flat',
               choices=['flat', 'vlan'],
               help='Type of the InfiniBand networks that the tests '
                    'create. Neutron allows only one flat network on each '
                    'physical network, so the tests that create one run '
                    'serially.'),
    cfg.IntOpt('segmentation_id',
               min=1,
               max=4094,
               help='Segmentation ID for the InfiniBand networks that the '
                    'tests create when network_type is vlan. If it is not '
                    'set, Neutron allocates one.'),
    cfg.IntOpt('network_mtu',
               help='MTU for the InfiniBand networks that the tests create, '
                    'for example 2044 for IPoIB in datagram mode with a 2048 '
                    'byte InfiniBand MTU. It needs the net-mtu-writable '
                    'extension. If it is not set, Neutron chooses the MTU.'),
    cfg.StrOpt('network_id',
               help='ID of an existing InfiniBand network to use instead of '
                    'creating one. Use this when the physical network '
                    'already has a flat network. The network must be on '
                    'physical_network, have a single segment and be shared. '
                    'The other options for creating networks are then '
                    'ignored.'),
    cfg.StrOpt('subnet_cidr',
               default='10.199.0.0/24',
               help='CIDR of the subnet on the InfiniBand networks that the '
                    'tests create.'),
]
