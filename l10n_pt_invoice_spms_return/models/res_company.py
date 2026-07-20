# Copyright 2026 NuoBiT Solutions SL - Deniz Gallo <dgallo@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


class ResCompany(models.Model):
    _inherit = "res.company"

    spms_adjustment_product_id = fields.Many2one(
        comodel_name="product.product",
        string="SPMS Adjustment Line Product",
        domain="[('sale_ok', '=', True), ('type', '=', 'service')]",
        help="Service product used for the adjustment line appended to a "
        "generated credit note when the official value differs from the "
        "prescription lines total.",
    )
