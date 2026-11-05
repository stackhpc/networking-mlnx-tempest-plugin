==============================
networking-mlnx-tempest-plugin
==============================

Tempest plugin for `networking-mlnx`_. It tests the ``mlnx_infiniband``
mechanism driver, ``neutron-mlnx-agent`` and ``eswitchd`` with real
InfiniBand SR-IOV hardware.

* Source: https://opendev.org/x/networking-mlnx-tempest-plugin
* Bugs: https://bugs.launchpad.net/networking-mlnx

.. _networking-mlnx: https://opendev.org/x/networking-mlnx

Tests
=====

``tests.api.test_agent.MlnxAgentTest``
  Checks that at least one alive ``neutron-mlnx-agent`` maps the InfiniBand
  physical network.

``tests.scenario.test_infiniband_ports.InfinibandPortTest``
  Boots servers with ``direct`` ports on an InfiniBand network, and attaches
  and detaches such ports on a running server. It checks that each port is
  bound with VIF type ``ib_hostdev`` to a virtual function on the server's
  host, that it has the DHCP client ID that the mechanism driver sets, and
  that it is released afterwards. The tests run serially, because Neutron
  allows only one flat network on each physical network and hosts have few
  virtual functions.

``tests.api.test_pkey.PkeyTest``
  Checks how networking-mlnx keeps InfiniBand ports in their partition key
  (pkey): that a project can't set a port's DHCP client ID on a network
  that ``mlnx_sdn_assist`` syncs to an SDN controller, that
  ``mlnx_infiniband`` binds ports to VLAN networks only when their
  partition keys are enforced, that it doesn't bind a port with the MAC
  address of a bound port on another network, that a direct port with a
  client ID other than the one derived from its MAC address, or with two
  client IDs, isn't bound, and that a synced network can't get a segment on an unsynced physical
  network. Each test creates its own VLAN networks, and is skipped unless
  it applies to the deployment. The tests bind ports without servers, by
  setting ``binding:host_id`` as an admin.

``tests.scenario.test_pkey_guest.PkeyGuestTest``
  Logs in to guests to check that a guest's InfiniBand port goes down
  when its port's ``admin_state_up`` is set to ``False`` and comes back up
  when it is set to ``True``, and that guests on different VLAN networks
  with the same subnet can't reach each other over IPoIB, while guests on
  the same network can. They are skipped unless ``guest_image_ref`` is
  set. The isolation test only runs if ``mlnx_sdn_assist`` syncs the
  network, or if ``allow_unenforced_pkeys`` is ``True`` and
  ``network_type`` is ``vlan``, in which case the subnet manager must
  have a partition for each VLAN ID in Neutron's range.

``tests.scenario.test_live_migration.InfinibandLiveMigrationTest``
  Live-migrates a guest with an InfiniBand port to another compute host
  with a ``neutron-mlnx-agent``. Nova unplugs the guest's virtual function
  before the migration and plugs one from the destination host after it.
  The test checks that the port is then ``ACTIVE`` and bound to the
  destination host with VIF type ``ib_hostdev`` and a PCI slot, that the
  guest's InfiniBand port becomes ``ACTIVE`` again with the same GUID, and
  that, once its IPoIB interface is configured again, the guest can ping a
  second guest over IPoIB, as it could before the migration. It is skipped unless
  ``live_migration`` is ``True``.

The tests in ``InfinibandPortTest`` check the port bindings through the
Neutron and Nova APIs. They don't log in to the guests, so any image that
boots works.

Which tests apply depends on whether ``mlnx_sdn_assist`` syncs the
InfiniBand physical network to an SDN controller, such as UFM. The tests
detect whether it restricts client IDs on the network by trying to create
a port with a DHCP client ID as the project, which it refuses on networks
whose physical networks are in its ``[sdn] physical_networks``, even with
``[sdn] sync_enabled = False``. By default they take that to mean that
the network is synced too; set ``sdn_sync`` if it isn't.

Requirements
============

* A cloud deployed with networking-mlnx, for example with its DevStack
  plugin.
* Free InfiniBand virtual functions on the compute hosts: one for
  ``test_boot_server_with_infiniband_port`` and two for
  ``test_attach_detach_infiniband_port``.
* Admin credentials, which the tests need to create provider networks and
  to see port bindings.
* For ``PkeyTest``, the ``agent`` extension and an alive
  ``neutron-mlnx-agent`` that maps the InfiniBand physical network, and for
  its segment test the ``segment`` extension.
* For ``PkeyTest`` and ``test_networks_are_isolated``, a VLAN range for the
  InfiniBand physical network in Neutron's ``network_vlan_ranges``, since
  they create VLAN networks without a segmentation ID. They are skipped if
  Neutron can't create them.
* For ``PkeyGuestTest``, an image with InfiniBand drivers, such as
  ``mlx5_ib`` and ``ib_ipoib``, and the ``ip`` and ``ping`` commands, whose
  user can run ``sudo`` without a password; a public network for floating IPs; interface attachment; and
  free virtual functions: one for ``test_vf_link_follows_port_admin_state``
  and two for ``test_networks_are_isolated``. The tests attach the
  InfiniBand ports after boot and configure them with ``ip``, and tell
  NetworkManager, if the image has it, not to manage them; cloud-init must
  not reconfigure hot-plugged interfaces. The guests' ports on the project
  network get ``[network] port_vnic_type``, if it is set.

  The guest tests have only been run with a Rocky Linux 10 image. Other
  images should work if nothing but NetworkManager manages hot-plugged
  interfaces. If the image has ``nmcli``, NetworkManager must be running,
  or the tests fail to configure the interfaces. If the image uses another tool, such as systemd-networkd,
  netplan or ifupdown, with a rule that matches new interfaces, for example
  one that runs DHCP on every interface, configure it to leave InfiniBand
  interfaces alone, or it may replace the address the tests set.
* For ``test_networks_are_isolated``, the subnet manager must give virtual
  functions only limited membership of the default partition, as
  networking-mlnx requires; otherwise the guests reach each other there
  and the test rightly fails.
* For ``InfinibandLiveMigrationTest``, the image, public network and
  interface attachment that ``PkeyGuestTest`` needs;
  ``[compute-feature-enabled] live_migration``; the ``binding-extended``
  extension, without which Nova doesn't live-migrate servers with SR-IOV
  ports; at least two enabled compute hosts, each with an alive
  ``neutron-mlnx-agent`` that maps the InfiniBand physical network and
  has the same host name as its ``nova-compute`` service; and free
  virtual functions for the two guests, and one more on the destination
  host, which may also have the second guest. The test
  migrates the guest to another of those hosts. It uses block migration
  if ``[compute-feature-enabled] block_migration_for_live_migration`` is
  ``True``; otherwise the hosts need shared storage for instances. Nova
  must have ``[compute] live_migration_wait_for_vif_plug = False`` on all
  compute hosts, since otherwise Nova waits for a ``network-vif-plugged``
  event of the InfiniBand port that doesn't come, and the migration fails
  or, with ``[DEFAULT] vif_plugging_is_fatal = False``, is delayed by
  ``vif_plugging_timeout``. The test can't
  detect that, so it only runs when ``live_migration`` is ``True``.

Installation
============

Install the plugin in the same environment as Tempest, from a checkout::

    git clone https://opendev.org/x/networking-mlnx-tempest-plugin
    pip install -e networking-mlnx-tempest-plugin

Check that Tempest finds it::

    tempest list-plugins

Configuration
=============

The tests are skipped unless the following are set in ``tempest.conf``::

    [service_available]
    networking_mlnx = True

    [networking_mlnx]
    physical_network = ibphysnet

The ``[networking_mlnx]`` section has these options:

``physical_network``
  Physical network that is mapped to an InfiniBand interface in the
  ``interface_mappings`` of ``neutron-mlnx-agent``.

``network_type``
  ``flat`` (the default) or ``vlan``: the type of the networks that the
  tests create.

``segmentation_id``
  Segmentation ID for ``vlan`` networks. If it is not set, Neutron allocates
  one.

``network_mtu``
  MTU for the networks that the tests create, for example ``2044``. It
  needs the ``net-mtu-writable`` extension. If it is not set, Neutron
  chooses the MTU.

``network_id``
  ID of an existing InfiniBand network to use instead of creating one. Use
  this when the physical network already has a flat network. The network
  must be on ``physical_network``, have a single segment and be shared. ``network_type``,
  ``segmentation_id``, ``network_mtu`` and ``subnet_cidr`` are then
  ignored.

``subnet_cidr``
  CIDR of the subnet on the networks that the tests create. The default is
  ``10.199.0.0/24``.

``sdn_sync``
  Whether ``mlnx_sdn_assist`` syncs ``physical_network`` to an SDN
  controller: ``auto`` (the default) detects it from whether client IDs
  are restricted, ``enabled`` or ``disabled`` say so. Set it to
  ``disabled`` if ``mlnx_sdn_assist`` is enabled with ``[sdn] sync_enabled
  = False``. The client ID tests always detect whether client IDs are
  restricted, unless this is ``enabled``.

``allow_unenforced_pkeys``
  The value of the Neutron server's ``[mlnx] allow_unenforced_pkeys``
  option. The default is ``False``.

``unsynced_physical_network``
  A physical network with a VLAN range in ``network_vlan_ranges`` that
  ``mlnx_sdn_assist`` doesn't sync, for the test that a synced network
  can't get a segment on it. That test is skipped if it is not set.

``guest_image_ref``
  ID of an image with InfiniBand drivers, for ``PkeyGuestTest`` and
  ``InfinibandLiveMigrationTest``. Their tests are skipped if it is not
  set.

``guest_flavor_ref``
  ID of the flavor for the guests of ``PkeyGuestTest`` and
  ``InfinibandLiveMigrationTest``. The default is ``[compute]
  flavor_ref``.

``guest_ssh_user``
  User to log in to the guests of ``PkeyGuestTest`` and
  ``InfinibandLiveMigrationTest`` as. The default is ``[validation]
  image_ssh_user``.

``live_migration``
  Whether to run ``InfinibandLiveMigrationTest``. The default is
  ``False``. Set it to ``True`` only if Nova has ``[compute]
  live_migration_wait_for_vif_plug = False`` on all compute hosts, and
  there are at least two compute hosts with free InfiniBand virtual
  functions on ``physical_network``.

Running the tests
=================

From the Tempest directory, or a Tempest workspace::

    tempest run --regex networking_mlnx_tempest_plugin

Running the tests at a site with an SDN controller
==================================================

With ``mlnx_sdn_assist`` syncing the InfiniBand physical network to an
SDN controller, such as UFM, configure the tests with VLAN networks, for
example::

    [service_available]
    networking_mlnx = True

    [networking_mlnx]
    physical_network = ibphysnet
    network_type = vlan
    # Optional: the tests detect it by default.
    sdn_sync = enabled
    # A physical network with a VLAN range that mlnx_sdn_assist doesn't
    # sync, if there is one.
    unsynced_physical_network = otherphysnet
    guest_image_ref = <ID of an image with InfiniBand drivers>
    guest_flavor_ref = <ID of a flavor for it>
    guest_ssh_user = rocky

    [network]
    public_network_id = <ID of the external network>

Then run, for example::

    tempest run --regex networking_mlnx_tempest_plugin

The tests act on the SDN controller:

* Every VLAN network and InfiniBand port that the tests create is synced
  to it, and removed again when the tests delete them.
* ``PkeyTest`` binds ports without servers. For each bound port the
  controller adds a GUID that no virtual function has to the network's
  partition key, until the test deletes the port.
* Each ``PkeyTest`` test creates up to two VLAN networks, and so partition
  keys, from Neutron's VLAN range for the physical network.
* Some ``PkeyTest`` tests delete ports whose binding to a host failed or
  was refused. networking-mlnx sends the deletion of every ``direct`` port
  with a host to the controller, including ports it never sent, such as
  the one with the same MAC address, and so GUID, as another port.

If the controller fails to remove a port, for example because the GUID
isn't in the partition key, networking-mlnx keeps retrying, by default
forever, and holds other ports with the same GUID meanwhile; see its
documentation on partition keys. Check the controller's jobs and
networking-mlnx's ``sdn_journal`` table after the first run.
