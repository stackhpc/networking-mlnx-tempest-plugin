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
    cfg.StrOpt('sdn_sync',
               default='auto',
               choices=['auto', 'enabled', 'disabled'],
               help='Whether the mlnx_sdn_assist mechanism driver syncs '
                    'physical_network to an SDN controller, such as UFM, '
                    'which adds InfiniBand ports to their network\'s '
                    'partition key. auto detects it: the driver refuses to '
                    'let a project set the DHCP client ID of a port on a '
                    'network whose physical network is in its [sdn] '
                    'physical_networks, so the tests try that. Without the '
                    'extra_dhcp_opt extension, auto means disabled. Set it '
                    'to disabled if mlnx_sdn_assist is enabled with [sdn] '
                    'sync_enabled = False, since it then still refuses '
                    'client IDs but syncs nothing. The tests of client IDs '
                    'always detect whether they are refused.'),
    cfg.BoolOpt('allow_unenforced_pkeys',
                default=False,
                help='The value of the [mlnx] allow_unenforced_pkeys option '
                     'of the Neutron server. If it is True, mlnx_infiniband '
                     'binds ports to VLAN networks on physical networks '
                     'that no SDN controller syncs.'),
    cfg.BoolOpt('shared_default_partition',
                default=False,
                help='True if the subnet manager makes every port a full '
                     'member of the default partition (ALL=full), as on a '
                     'fabric shared with hosts that rely on it. Guests on '
                     'different networks can then reach each other over '
                     'the default partition, so the test that they can\'t '
                     'is skipped.'),
    cfg.StrOpt('unsynced_physical_network',
               help='A physical network that Neutron has a VLAN range for '
                    'in network_vlan_ranges, but that mlnx_sdn_assist does '
                    "not sync, because it isn't in its [sdn] "
                    'physical_networks. A test checks that a network on '
                    "physical_network can't also have a segment on it. "
                    'The test is skipped when it is not set.'),
    cfg.StrOpt('guest_image_ref',
               help='ID of an image with InfiniBand drivers, such as '
                    'mlx5_ib and ib_ipoib, and the ip command, for the '
                    'tests that log in to guests. The tests are skipped '
                    'when it is not set.'),
    cfg.StrOpt('guest_flavor_ref',
               help='ID of the flavor for the guests of the tests that log '
                    'in to them. If it is not set, [compute] flavor_ref is '
                    'used.'),
    cfg.StrOpt('guest_ssh_user',
               help='User to log in to guests booted from guest_image_ref '
                    'as. It must be able to run commands with sudo. If it '
                    'is not set, [validation] image_ssh_user is used.'),
    cfg.BoolOpt('live_migration',
                default=False,
                help='Whether to run the test that live-migrates a guest '
                     'with an InfiniBand port. It needs Nova\'s [compute] '
                     'live_migration_wait_for_vif_plug = False on all '
                     'compute hosts, or the migration fails, or is delayed '
                     'by vif_plugging_timeout, waiting for a '
                     'network-vif-plugged event of the InfiniBand port. '
                     'The test cannot detect that, so it is skipped '
                     'unless this is True. It also needs guest_image_ref, '
                     '[compute-feature-enabled] live_migration, the '
                     'binding-extended extension, and two '
                     'compute hosts with free virtual functions on '
                     'physical_network, each with an alive '
                     'neutron-mlnx-agent that maps it.'),
]
