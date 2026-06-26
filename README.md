# OxigenSalud Odoo Non-standalone Addons
<!-- /!\ Non OCA Context : Set here the badge of your runbot / runboat instance. -->
[![Pre-commit Status](https://github.com/oxigensalud/odoo-nonstandalone-addons/actions/workflows/pre-commit.yml/badge.svg?branch=14.0)](https://github.com/oxigensalud/odoo-nonstandalone-addons/actions/workflows/pre-commit.yml?query=branch%3A14.0)
[![Build Status](https://github.com/oxigensalud/odoo-nonstandalone-addons/actions/workflows/test.yml/badge.svg?branch=14.0)](https://github.com/oxigensalud/odoo-nonstandalone-addons/actions/workflows/test.yml?query=branch%3A14.0)
[![codecov](https://codecov.io/gh/oxigensalud/odoo-nonstandalone-addons/branch/14.0/graph/badge.svg)](https://codecov.io/gh/oxigensalud/odoo-nonstandalone-addons)
<!-- /!\ Non OCA Context : Set here the badge of your translation instance. -->

<!-- /!\ do not modify above this line -->

Non-standalone addons for OxigenSalud.

This repository contains free and open-source Odoo modules developed by OxigenSalud.
However, these modules **depend on private, non-free modules** that are not included here.
As a result, they are **not usable out-of-the-box** unless you have access to the required internal dependencies.

This repository is public for transparency, documentation, and CI/CD purposes.
It is not intended for direct use by third parties.

## Why This Repository Is Public

While the modules in this repository depend on internal, non-public components and are not directly usable by third parties, we maintain this repository as public for practical and operational reasons:

- **Visibility for Partners and Teams**: External collaborators, auditors, or integration partners may need to review parts of our codebase, even if they can't execute it end-to-end.
- **Documentation and Change Tracking**: Public version control provides a clear history of changes, improving maintainability and traceability.
- **Future-Proofing**: Some private dependencies may eventually be replaced or made public. Keeping this repository open simplifies transitions without needing structural refactoring.
- **Knowledge Sharing**: Even if the modules are not fully functional on their own, their structure and implementation may serve as useful references for internal or industry peers.

This repository is **not intended for direct use** by third parties and should be treated as part of a larger system where internal components are expected to be available.

## Licenses

This repository is licensed under the [LGPL-3.0](LICENSE).

However, each module may use a different license, as long as it complies with OxigenSalud’s internal licensing policy.
Please consult each module’s `__manifest__.py` file, which includes a `license` field indicating the specific license used.

## CI/CD Notice

> ⚠️ **Note on CI/CD:**
> Modules in this repository depend on private Odoo addons that are not accessible to public or internal CI environments.
> As a result, automated pipelines are **limited to lightweight checks**, such as formatting, syntax, or pre-commit validations.
> Functional testing, dependency resolution, or runtime validation **is not possible** in this repository due to the absence of required private code.
> This repository is maintained publicly for documentation, visibility, and version control, but is not part of a standalone build or test process.
