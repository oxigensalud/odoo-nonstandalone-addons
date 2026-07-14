from openupgradelib import openupgrade


@openupgrade.migrate()
def migrate(env, version):
    openupgrade.add_fields(
        env,
        [
            (
                "spms_reference_invoice_id",
                "account.move",
                False,
                "many2one",
                False,
                "l1n_pt_invoice_spms",
            ),
            (
                "spms_period_start",
                "account.move",
                False,
                "date",
                False,
                "l1n_pt_invoice_spms",
            ),
        ],
    )
