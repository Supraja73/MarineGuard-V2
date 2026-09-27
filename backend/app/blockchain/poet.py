"""
poet.py
-------
Simulated Proof of Elapsed Time (PoET) leader election.

Real PoET (as used in Hyperledger Sawtooth) relies on an Intel SGX
trusted hardware enclave to *prove* a validator genuinely waited its
randomly-assigned time before claiming the right to publish a block,
rather than lying about a shorter wait. We don't have SGX hardware
available, so this module implements the same software-only "PoET
simulator" mode Sawtooth itself falls back to for development and
testing without real enclaves: every validator draws a random wait
time, and whichever validator's timer elapses first wins the election.

What this module intentionally does NOT do: provide hardware-backed
attestation that a validator can't cheat on its draw. That guarantee is
exactly what real SGX-based PoET adds and simulation cannot. Here,
"winning" the election only decides who gets *credited* as the proposer
of a block — it has no bearing on the block's actual integrity, which
still comes entirely from the SHA-256 hash-linking in ledger.py.

--------------------------------------------------------------------
NEW: real, measured wait times (Validator Wait Time graph feature)
--------------------------------------------------------------------
This module previously reported each validator's randomly *drawn*
target as if it were the "wait time" — the number shown was the input
to random.uniform(), never anything actually timed. That was fine for
picking a winner (shortest draw wins, regardless of whether it's timed
or not) but it is not a genuine measurement, and the new live Wait
Time graph must only ever plot real numbers.

elect_proposer() now actually makes every validator wait its drawn
duration (via asyncio.sleep) and independently times that wait with
time.perf_counter_ns() — the highest-resolution, monotonic clock
Python exposes, so a system clock adjustment mid-wait can't corrupt the
measurement the way time.time() could. The number returned per
validator is the REAL elapsed time of that wait, not the random draw
that seeded it. The election rule itself is unchanged: whichever
validator's timer elapses first is still the winner.

All validators wait concurrently (asyncio.gather), so the total time
this function takes is bounded by the single slowest draw, not the sum
of all four — this mirrors real PoET, where validators race in
parallel, not in sequence.
"""

import asyncio
import random
import time

# A small fixed pool of simulated validator nodes. In a real deployment
# these would be independent physical machines each running their own
# validator process; here they're lightweight stand-ins living inside
# this one backend process. That's enough to demonstrate genuine
# leader-election semantics (a real lottery with a real winner) without
# needing an actual multi-node cluster.
#
# UNCHANGED — still the same 4 validators the consensus algorithm has
# always used. proposer_id / poet_wait_ms / poet_draws stored on each
# Block row still use these exact ids, so existing ledger history and
# the "race" breakdown already shown on the Blockchain page keep working
# unmodified.
VALIDATORS = ["validator-alpha", "validator-bravo", "validator-charlie", "validator-delta"]

# Human-readable labels used ONLY for the new live graph and console
# round logs ("Validator 1", "Validator 2", ...). Presentation-only —
# nothing stored in the database changes; proposer_id in the ledger
# still stores "validator-alpha" etc. exactly as before.
DISPLAY_NAMES = {
    "validator-alpha": "Validator 1",
    "validator-bravo": "Validator 2",
    "validator-charlie": "Validator 3",
    "validator-delta": "Validator 4",
}

# Wait-time bounds (milliseconds). Kept short since this runs inline on
# every block append — real PoET wait times are much longer, but the
# election mechanic (shortest random draw wins) is identical regardless
# of scale. UNCHANGED from before this feature was added.
MIN_WAIT_MS = 40
MAX_WAIT_MS = 380


async def _timed_wait(validator_id: str, target_ms: float) -> tuple[str, float]:
    """Make one validator actually wait ~target_ms, and report the REAL
    elapsed time measured with a high-resolution timer — never the
    target itself. asyncio.sleep is not perfectly exact (it's "sleep for
    at least this long", subject to event-loop scheduling), which is
    exactly why we re-measure with perf_counter_ns() afterwards instead
    of trusting the requested duration.
    """
    start = time.perf_counter_ns()
    await asyncio.sleep(target_ms / 1000.0)
    end = time.perf_counter_ns()
    elapsed_ms = (end - start) / 1_000_000
    return validator_id, round(elapsed_ms, 2)


async def elect_proposer() -> dict:
    """Run one PoET election.

    Every validator in the pool independently draws a random target wait
    time, then genuinely waits that long while a high-resolution timer
    measures the real elapsed time (see _timed_wait). Whichever
    validator's REAL measured wait is shortest is elected to propose the
    next block — the same "shortest wait wins" rule as before, just now
    judged on a real measurement instead of the random draw itself.
    Returns the winner plus every validator's measured wait so callers
    (the Blockchain Logs UI, and the new live Wait Time graph) can
    display the full race, not just the outcome.
    """
    targets = {v: random.uniform(MIN_WAIT_MS, MAX_WAIT_MS) for v in VALIDATORS}

    # Race all four validators concurrently and time each one for real.
    measured_pairs = await asyncio.gather(
        *(_timed_wait(v, target_ms) for v, target_ms in targets.items())
    )
    measured = dict(measured_pairs)

    winner = min(measured, key=measured.get)
    return {
        "proposer_id": winner,
        "wait_time_ms": measured[winner],
        "all_draws": measured,  # kept as "all_draws" for backward compatibility with existing callers/UI
    }
