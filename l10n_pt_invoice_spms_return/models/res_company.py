# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import _, fields, models
from odoo.exceptions import UserError


class ResCompany(models.Model):
    _inherit = "res.company"

    spms_adjustment_product_id = fields.Many2one(
        comodel_name="product.product",
        string="SPMS Adjustment Line Product",
        domain="['&', '&', ('sale_ok', '=', True), ('type', '=', 'service'),"
        " '|', ('company_id', '=', False), ('company_id', '=', id)]",
        help="Service product used for the adjustment line appended to a "
        "generated credit note when the official value differs from the "
        "prescription lines total.",
    )
    spms_estimation_tax_id = fields.Many2one(
        comodel_name="account.tax",
        string="SPMS Estimation Tax",
        domain="[('company_id', '=', id), ('type_tax_use', '=', 'sale'),"
        " ('amount_type', '=', 'percent')]",
        help="Sales VAT the SPMS conference applies in its error file: used "
        "to compute the with-VAT credit estimate and to cross-check the "
        "file's own allowed-with-VAT column. The credit-note taxes always "
        "come from the original invoice lines, never from this tax.",
    )

    def _get_spms_tax_factor(self):
        self.ensure_one()
        tax = self.spms_estimation_tax_id
        if not tax:
            raise UserError(
                _(
                    "Configure the SPMS estimation tax on company %s before "
                    "processing SPMS returns."
                )
                % self.display_name
            )
        return 1 + tax.amount / 100.0
