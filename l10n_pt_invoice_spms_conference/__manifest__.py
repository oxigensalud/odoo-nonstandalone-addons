# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

{
    "name": "L10n Pt Invoice Spms Conference",
    "summary": "SPMS conference results log and credit note generation",
    "version": "14.0.1.0.0",
    "development_status": "Beta",
    "license": "AGPL-3",
    "author": "NuoBiT Solutions SL, Oxigen Salud SA",
    "website": "https://github.com/oxigensalud/odoo-nonstandalone-addons",
    "category": "Accounting",
    "depends": [
        "l10n_pt_invoice_spms",
    ],
    "data": [
        "security/spms_conference_security.xml",
        "security/ir.model.access.csv",
        "views/account_move_views.xml",
        "views/res_company_views.xml",
        "views/spms_conference_result_error_views.xml",
        "views/spms_conference_result_views.xml",
    ],
}
