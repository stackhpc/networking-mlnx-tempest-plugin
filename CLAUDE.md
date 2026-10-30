# networking-mlnx-tempest-plugin

Tempest plugin for networking-mlnx: API and scenario tests for the
`mlnx_infiniband` mechanism driver, `neutron-mlnx-agent` and `eswitchd`
on InfiniBand SR-IOV hardware. Hosted on opendev as
`x/networking-mlnx-tempest-plugin`, built with pbr and reviewed on Gerrit
(review.opendev.org). Like all Tempest plugins it is branchless: the same
code must work against every supported OpenStack release.

## Commits

- Follow the 50/72 rule: subject line of at most 50 characters, a blank
  line, then a body wrapped at 72 characters.
- Always commit with `git commit --signoff`, so every commit has a
  `Signed-off-by:` trailer. When amending or rebasing, keep it.
- Credit AI assistance with an `Assisted-By:` trailer (for example
  `Assisted-By: Claude Opus 5.5`) in addition to `Signed-off-by:`, not
  `Co-Authored-By:`. Put `Assisted-By:` at the end of the message; the
  Gerrit commit-msg hook then adds `Change-Id:` and `--signoff` adds
  `Signed-off-by:` after it.
- One logical change per commit. Related changes go in a stack of commits
  on one branch and are pushed with `git review`.
- Never push to Gerrit without being asked.
- Release notes are for the people who install the package. Add a reno
  note (`releasenotes/notes/`) for changes that affect them, such as
  dependency, supported-runtime, configuration or behaviour changes. Don't
  add one for changes that only affect developers, such as tox, CI, tests,
  docs tooling or packaging internals. Follow the style of the existing
  notes.

## Workflow for each change

1. Implement the change.
2. Run `tox -e pep8,docs,releasenotes`. Each must pass.
3. Run the changed tests against a cloud with networking-mlnx and
   InfiniBand hardware, for example a DevStack deployed with the
   networking-mlnx DevStack plugin:

       tempest run --regex networking_mlnx_tempest_plugin

   The upstream gate has no InfiniBand hardware, so it only runs pep8,
   docs and release notes jobs.
4. Get a code review from a separate agent with fresh context (not a
   self-review). Address its comments and re-run the tests.
5. Commit.

## Writing tests

- Only use Tempest's stable interfaces where they exist (`tempest.lib`,
  `tempest.test`, `tempest.config`) and the service clients from
  `tempest.clients`. `tempest.common.waiters` and `tempest.common.utils`
  are widely used by plugins and are acceptable.
- Give every test a unique `@decorators.idempotent_id` and never change
  it once merged.
- Clean up every resource a test creates, with `addCleanup` or
  `addClassResourceCleanup`.
- Tests must skip, not fail, when networking-mlnx isn't configured.
- Hosts have few InfiniBand virtual functions, and Neutron allows only one
  flat network on each physical network, so tests that create networks or
  boot servers with InfiniBand ports run serially.
