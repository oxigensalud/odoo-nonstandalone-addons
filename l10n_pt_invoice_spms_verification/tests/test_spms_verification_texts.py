# Copyright 2026 NuoBiT Solutions SL - Eric Antones <eantones@nuobit.com>
# License AGPL-3.0 or later (https://www.gnu.org/licenses/agpl).

import re
from pathlib import Path

from odoo.modules.module import get_module_path
from odoo.tests import tagged
from odoo.tests.common import SavepointCase

# The interval and the hour of a scheduled action are the administrator's to
# set: a text that names one goes stale the day it changes. A text names one
# when a word of cadence, or a clock time, stands within six words of a word
# about the action. The words of a phrase may be split by the line wrapping
# of the file.
ACTION = r"""(?:
    pass(?:es)? | runs? | running | execut\w+ | poll\w* | scheduled
    | actions? | crons? | sync\w* | asks? | asked
    | acci(?:ó|ons|ón|ones) | aç(?:ão|ões)
    | passad(?:a|es) | pasadas? | passage(?:m|ns)
    | execuci\w+ | ejecu[ct]\w+ | execuç\w+ | s'executa
    | planificad\w+ | agendad\w+
    | pregunt\w+ | pergunt\w+
)"""
# «horari», «horario» and «horário» are also the noun for a schedule: only
# the feminine adjective names a cadence. A clock time is not part of a date.
CADENCE = r"""(?:
    hourly | daily | nightly | weekly | monthly
    | (?:every|each) \W+ (?:\w+\W+)? (?:minutes?|hours?|days?|nights?|weeks?)
    | (?:once|twice|\d+\W+times) \W+ (?:an?|per) \W+ (?:hour|day|night|week)
    | per \W+ (?:hour|day|night)
    | hor[àaá]ri(?:a|es|as)
    | di[àaá]ri(?:a|o|es|as|os)?
    | setmanals? | semana(?:l|les|is) | mensuals? | mensa(?:l|is)
    | nocturn(?:a|o|es|as|os)? | noturn(?:a|o|as|os)
    | cada \W+ (?:\d+\W+)?
      (?:minuts?|minutos?|hor(?:a|es|as)|dia|dies|día|días|dias|nit|noche|noite
         |setmana|semana)
    | (?:un\W+cop|una\W+vegada|dos\W+cops|dues\W+vegades
         |una\W+vez|dos\W+veces|uma\W+vez|duas\W+vezes)
      \W+ (?:al|a\W+la|per|por|ao|à) \W+
      (?:dia|día|hora|nit|noche|noite|setmana|semana)
    | (?:per|por) \W+ hora
    | d['e]\W*hora \W+ (?:en|em|a) \W+ hora
    | di[àa]riament | diariamente | setmanalment | semanalmente
    | (?<!\d\d-\d\d\W) (?<![\d:]) \d{1,2}:\d{2} (?![\d:])
)"""
NEAR = r"\W+ (?:\w+\W+){0,6}?"
INTERVAL = re.compile(
    r"(?<!\w)(?:%(action)s %(near)s %(cadence)s"
    r" | %(cadence)s %(near)s %(action)s)(?!\w)"
    % {"action": ACTION, "near": NEAR, "cadence": CADENCE},
    re.IGNORECASE | re.VERBOSE,
)
TEXT_SUFFIXES = (".py", ".xml", ".md", ".rst", ".html", ".po", ".pot", ".csv")

NAMES_AN_INTERVAL = (
    "the next hourly pass asks again on its own",
    "its hourly input action processes the document",
    "the hourly input sync of the framework",
    "The scheduled action runs hourly out of the box",
    "the action asks the CCF once a day",
    "the polling runs every two hours",
    "the scheduled action runs every night",
    "the scheduled action runs every 15 minutes",
    "the nightly run asks again",
    "the daily pass of the scheduled action",
    "it polls the CCF twice a day",
    "one pass per hour",
    "the scheduled action runs at 05:00 UTC",
    'the next hourly pass asks "\n"again',
    "la següent passada horària torna a preguntar tota sola",
    "la passada diària de l'acció planificada",
    "l'acció planificada s'executa un cop al dia",
    "l'acció planificada pregunta cada hora",
    "l'acció planificada s'executa cada 24 hores",
    "l'acció planificada pregunta diàriament",
    "la siguiente pasada horaria vuelve a preguntar por sí sola",
    "la pasada diaria de la acción planificada",
    "la acción planificada se ejecuta una vez al día",
    "la acción planificada se ejecuta de hora en hora",
    "la acción planificada se ejecuta cada noche",
    "a passagem horária seguinte volta a perguntar",
    "a passagem diária da ação agendada",
    "a ação agendada é executada uma vez por dia",
    "a ação agendada é executada de hora em hora",
    "a ação agendada pergunta diariamente",
    "the next hourly run asks again on its own",
    "la següent execució horària torna a preguntar tota sola",
    "la siguiente ejecución horaria vuelve a preguntar por sí sola",
    "a execução horária seguinte volta a perguntar",
)
NAMES_NO_INTERVAL = (
    "the next run of the scheduled action asks again on its own",
    "the scheduled action runs at the interval set on it",
    "The verification takes weeks",
    "the daily rate of the treatment",
    "price per hour",
    "each day of the prescription",
    '@freeze_time("2026-09-29 04:00:00")',
    '"POT-Creation-Date: 2026-09-26 17:21+0000\\n"',
    "la següent execució de l'acció planificada torna a preguntar tota sola",
    "l'acció planificada s'executa segons l'horari configurat",
    "zona horària",
    "la siguiente ejecución de la acción planificada vuelve a preguntar por sí sola",
    "la acción planificada se ejecuta según el horario configurado",
    "Asiento de Diario",
    "a execução seguinte da ação agendada volta a perguntar por si só",
    "cada dia de tratamento",
    "fuso horário",
    "Mov. de Diário",
)


@tagged("post_install", "-at_install")
class TestSpmsVerificationTexts(SavepointCase):
    def test_no_text_names_an_interval(self):
        module = Path(get_module_path("l10n_pt_invoice_spms_verification"))
        named = []
        for path in sorted(module.rglob("*")):
            if path.suffix in TEXT_SUFFIXES and path.name != Path(__file__).name:
                for found in INTERVAL.finditer(path.read_text(encoding="utf-8")):
                    named.append("%s: %s" % (path.relative_to(module), found.group()))
        self.assertEqual(named, [])

    def test_a_cadence_next_to_the_action_is_found(self):
        for text in NAMES_AN_INTERVAL:
            with self.subTest(text=text):
                self.assertTrue(INTERVAL.search(text))

    def test_the_vocabulary_of_other_matters_is_left_alone(self):
        for text in NAMES_NO_INTERVAL:
            with self.subTest(text=text):
                self.assertFalse(INTERVAL.search(text))
