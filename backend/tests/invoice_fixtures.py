"""Invoices, written as the text a forwarder's PDF actually carries — refunds included."""

from __future__ import annotations

#: A French forwarder's invoice for one container: THC, ocean freight, customs clearance, VAT.
FRENCH_ONE_CONTAINER = [
    "TRANSDEMO SAS",
    "12 quai de la Marine, 76600 Le Havre - TVA FR12345678901",
    "FACTURE N° FA-2026-0912",
    "Date : 12/03/2026",
    "Client : Acme Import SAS",
    "Dossier : MEDUSH2604417   B/L MEDUSH2604417",
    "Conteneur MSCU4821990  40HC",
    "",
    "Designation                                  Quantite   PU        Montant",
    "Fret maritime CNNGB / FRLEH                  1          3 000,00  3 000,00",
    "THC destination Le Havre                     2          137,50    275,00",
    "Dedouanement import                          1          130,00    130,00",
    "Camionnage Le Havre - Rouen                  1          425,00    425,00",
    "",
    "Total HT                                                          3 830,00",
    "TVA 20%                                                             766,00",
    "Total TTC                                                         4 596,00 EUR",
]

#: The same forwarder, two containers on one invoice — the case that needs a human eye.
FRENCH_TWO_CONTAINERS = [
    "TRANSDEMO SAS",
    "FACTURE N° FA-2026-0918",
    "Date : 18/03/2026",
    "Dossier : MEDUSH2604417",
    "Conteneurs : MSCU4821990 et TGHU7245081",
    "",
    "THC destination MSCU4821990                                        137,50",
    "THC destination TGHU7245081                                        137,50",
    "Surestaries MSCU4821990 3 jours                                    360,00",
    "Camionnage TGHU7245081                                             425,00",
    "",
    "Total HT                                                         1 060,00",
    "TVA 20%                                                            212,00",
    "Total TTC                                                        1 272,00 EUR",
]

#: A refund, written the five ways refunds are actually written. Every one of them adds up: the
#: arithmetic check blesses all five, which is precisely why it cannot be the thing that decides.
CREDIT_NOTE_MINUS_GLUED = [
    "TRANSDEMO SAS",
    "AVOIR N° A-2026-118",
    "Date : 14/03/2026",
    "Annule la facture FA-2026-0912 (surcharge facturée à tort)",
    "Conteneur MSCU4821990",
    "",
    "Fret maritime Shanghai / Le Havre                                 -2 450,00",
    "",
    "Total HT                                                          -2 450,00",
    "TVA 20%                                                             -490,00",
    "Total TTC                                                         -2 940,00 EUR",
]

#: Accountants write a negative in parentheses; the minus never appears at all.
CREDIT_NOTE_PARENTHESES = [
    "TRANSDEMO SAS",
    "NOTE DE CRÉDIT N° NC-2026-044",
    "Date : 20/03/2026",
    "Conteneur MSCU4821990",
    "",
    "THC destination Le Havre                                            (275,00)",
    "",
    "Total HT                                                            (275,00)",
    "TVA 20%                                                              (55,00)",
    "Total TTC                                                           (330,00) EUR",
]

#: An English credit note from the same carrier, with the sign printed after the figure.
CREDIT_NOTE_TRAILING_MINUS = [
    "PACIFIC FORWARDING INC",
    "CREDIT NOTE No CN-77120",
    "Date: 2026-03-22",
    "Container MSCU4821990",
    "",
    "Ocean freight Ningbo / Le Havre                                   1,200.00-",
    "",
    "Subtotal                                                          1,200.00-",
    "VAT 20%                                                             240.00-",
    "Total due                                                         1,440.00- USD",
]

#: The typographic minus a PDF font prints instead of the hyphen, loose in front of the figure.
#: Read as text rather than through a PDF: the test writer's font is latin-1 and has no such glyph.
CREDIT_NOTE_UNICODE_MINUS = [
    "TRANSDEMO SAS",
    "AVOIR N° A-2026-205",
    "Date : 25/03/2026",
    "Conteneur MSCU4821990",
    "",
    "Camionnage Le Havre - Rouen                                        \u2212 425,00",
    "",
    "Total HT                                                           \u2212 425,00",
    "TVA 20%                                                             \u2212 85,00",
    "Total TTC                                                          \u2212 510,00 EUR",
]

#: The commonest of all, and the one no arithmetic can catch: every figure is positive, and only
#: the word at the top says the money is going the other way.
CREDIT_NOTE_POSITIVE_FIGURES = [
    "TRANSDEMO SAS",
    "AVOIR N° A-2026-211",
    "Date : 28/03/2026",
    "Annule et remplace la facture FA-2026-0918",
    "Conteneur MSCU4821990",
    "",
    "Surestaries 3 jours                                                  360,00",
    "",
    "Total HT                                                             360,00",
    "TVA 20%                                                               72,00",
    "Total TTC                                                            432,00 EUR",
]

#: The four that go through a PDF; the typographic one is read as text, see above.
CREDIT_NOTES = [
    CREDIT_NOTE_MINUS_GLUED,
    CREDIT_NOTE_PARENTHESES,
    CREDIT_NOTE_TRAILING_MINUS,
    CREDIT_NOTE_POSITIVE_FIGURES,
]

#: An invoice with a commercial discount on it: one charge, one credit, and a total that proves it.
FRENCH_WITH_DISCOUNT = [
    "TRANSDEMO SAS",
    "FACTURE N° FA-2026-0931",
    "Date : 31/03/2026",
    "Conteneur MSCU4821990",
    "",
    "Fret maritime CNNGB / FRLEH                                        2 450,00",
    "Remise commerciale                                                  -150,00",
    "",
    "Total HT                                                           2 300,00",
    "TVA 20%                                                              460,00",
    "Total TTC                                                          2 760,00 EUR",
]

#: A header with an exchange rate: the forwarder prints the day's rate for the origin leg, and the
#: whole invoice used to be read as dollars because of it.
EXCHANGE_RATE_IN_HEADER = [
    "ACME TRANSIT SAS",
    "Taux de change USD/EUR 1,0850",
    "Facture n° F-2026-118",
    "Fret maritime 2 450,00 EUR",
]

#: The same trap at the other end of the page: the supplier's dollar account under the totals.
DOLLAR_IBAN_IN_FOOTER = [
    "TRANSDEMO SAS",
    "Facture n° F-2026-119",
    "Conteneur MSCU4821990",
    "",
    "Fret maritime CNNGB / FRLEH                                        2 450,00",
    "",
    "Total HT                                                           2 450,00 EUR",
    "Paiement par virement - compte USD, IBAN US64 SVBK 3300 9605 8790 1863",
]

#: An invoice in USD, dated a day for which we have no reference rate in the tests.
USD_INVOICE = [
    "PACIFIC FORWARDING INC",
    "INVOICE No INV-77120",
    "Date: 2026-03-05",
    "Container MSCU4821990",
    "",
    "Ocean freight Ningbo / Le Havre                                 4,200.00",
    "Terminal handling charge                                          310.00",
    "",
    "Total due                                                      4,510.00 USD",
]
