# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

from odoo import fields, models


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
    spms_adjustment_limit = fields.Monetary(
        string="SPMS Adjustment Limit",
        currency_field="currency_id",
        default=0.05,
        help="Largest difference, taxes included, between the official "
        "value and the credit-note lines total that the adjustment line "
        "may absorb: rounding cents. A larger difference holds the result "
        "in error for review instead of generating the credit note.",
    )

    _sql_constraints = [
        (
            "spms_adjustment_limit_positive",
            "CHECK(spms_adjustment_limit >= 0)",
            "The SPMS adjustment limit cannot be negative.",
        ),
    ]
