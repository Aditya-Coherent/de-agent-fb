"""Assumptor: realistic, calibrated assumptions for numbers no source gives,
with a reward-driven learning loop (gym.py) trained on hidden official
statistics. See agent.py (the estimate), policy.py (what is learned),
strategies.py (the actions) and gym.py (episodes, train, evaluate).
"""
from .agent import assume
from .covariates import Covariates
from .policy import Policy

__all__ = ["assume", "Covariates", "Policy"]
