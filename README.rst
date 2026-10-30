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

The scenario tests check the port bindings through the Neutron and Nova
APIs. They don't log in to the guests, so any image that boots works.

Requirements
============

* A cloud deployed with networking-mlnx, for example with its DevStack
  plugin.
* Free InfiniBand virtual functions on the compute hosts: one for
  ``test_boot_server_with_infiniband_port`` and two for
  ``test_attach_detach_infiniband_port``.
* Admin credentials, which the tests need to create provider networks and
  to see port bindings.

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

Running the tests
=================

From the Tempest directory, or a Tempest workspace::

    tempest run --regex networking_mlnx_tempest_plugin
