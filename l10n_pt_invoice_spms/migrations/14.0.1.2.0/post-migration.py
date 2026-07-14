from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    moves = env["account.move"].search(
        [
            ("partner_id.spms_information", "=", True),
            ("move_type", "in", ["out_invoice", "out_refund"]),
        ]
    )
    moves._compute_spms_period_start()
    for move in moves.sorted(key=lambda m: m.date, reverse=True):
        move._compute_spms_reference_invoice_id()
