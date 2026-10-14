"""Triangulation layer shared by every demand-side approach (2A, 2B, 2C).

    final = inverse-variance blend (in log space) of
        B  the approach's own bottom-up estimate, with an uncertainty sigma_B
           its adapter derives from its evidence quality, and
        P  the consensus of independently published market sizes, with an
           uncertainty sigma_P from how much the publishers disagree.

Modules:
    published.py  find, verify and normalise published global market sizes -> P, sigma_P
    combine.py    the inverse-variance blend + disagreement flag
    adapters.py   sigma_B for each approach's result format
"""
