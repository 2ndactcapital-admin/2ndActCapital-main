"""_verify_oneoff — drive ONE regression verify's own seed + its first fixture section, then its own teardown.

    VERIFY_NESTED=1 timeout 600 doppler run -- apps/api/venv/bin/python -u apps/api/scripts/_verify_oneoff.py \
        verify_rep1.py [--section NAME]

WHY (VERIFYFIX1)
----------------
A full verify can take 10–45 minutes and WSL memory is limited, so a sprint that migrates many verifies proves each
one with ``--self-checks-only`` plus a short one-off over the verify's OWN code: nothing here re-implements a check.

WHAT IT RUNS (all the verify's own functions, in its own ``main_async`` order)
-----------------------------------------------------------------------------
1. the verify's connection exactly as its main does (``admin_dsn`` → ``connect``; ``install_stubs`` if it has one);
2. ``teardown`` (the pre-run cleanup, by fixture ids) and ``counts`` (its counted tables, live-written ones by
   fixture ids — _verify_scope);
3. its seed function (``seed`` / ``seed_users``) — which now ends with the shared roles assertion
   (``VR.check_roles_as_meant``: every fixture role is a user_roles grant the app decides as meant);
4. the FIRST section its ``main_async`` runs after the seed (or ``--section NAME``) — skipped with a [FIND] when the
   verify has none;
5. ``teardown`` again, ``leftovers`` == 0 and every counted table back at its pre-run count.

Every check prints through the verify's own ``check``; the final line is ``TOTAL: N PASS, M FAIL`` from the verify's
own counters (TOTAL on every exit path — _verify_total.install_guard). Exit 0 only with 0 FAIL.
"""
from __future__ import annotations

import ast
import asyncio
import importlib
import inspect
import os
import pathlib
import sys

HERE = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))

import _verify_total as VT  # noqa: E402

MOD = None


def _counts():
    return (getattr(MOD, "_n_pass", 0), getattr(MOD, "_n_fail", 0)) if MOD else (0, 0)


def main_order(path: pathlib.Path) -> list[str]:
    """The function names main_async passes to run(name, fn, …) / awaits directly, in source order."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    fn = next((n for n in tree.body if isinstance(n, ast.AsyncFunctionDef) and n.name in ("main_async", "main")), None)
    out = []
    for node in ast.walk(fn) if fn else ():
        if isinstance(node, ast.Call):
            f = node.func
            if isinstance(f, ast.Name) and f.id == "run" and len(node.args) >= 2 and isinstance(node.args[1], ast.Name):
                out.append((node.lineno, node.args[1].id))
            elif isinstance(f, ast.Name) and f.id.startswith(("seed", "y_")):
                out.append((node.lineno, f.id))
    return [n for _, n in sorted(set(out))]


async def _call(fn, conn):
    args = [conn] if len(inspect.signature(fn).parameters) >= 1 else []
    res = fn(*args)
    if asyncio.iscoroutine(res):
        res = await res
    return res


async def drive(name: str, section: str | None) -> int:
    global MOD
    path = HERE / name
    MOD = importlib.import_module(path.stem)
    check, find = MOD.check, getattr(MOD, "find", lambda *a, **k: print("[FIND]", *a))
    order = main_order(path)
    seed_name = next((n for n in order if n in ("seed", "seed_users")), None) or \
        next((n for n in ("seed", "seed_users") if hasattr(MOD, n)), None)
    after = order[order.index(seed_name) + 1:] if seed_name in order else []
    # VERIFYFIX1 fix 1: the y_ sections main runs BEFORE the seed run first, as in a real run — they hold no fixtures
    # but some seeds read state they fill (verify_rep7's y_seed_literals sets S["instrument_class"]).
    pre_seed = [] if "--no-pre" in sys.argv else [
        n for n in (order[:order.index(seed_name)] if seed_name in order else [])
        if n.startswith("y_") and hasattr(MOD, n)]
    pick = section or next((n for n in after if n.startswith("y_") and hasattr(MOD, n)), None)
    print(f"[INFO] one-off {name}: pre-seed = {pre_seed}; seed = {seed_name}; section = {pick}; main order = {order}")
    dsn, how = await MOD.admin_dsn()
    if not dsn:
        check(False, "a working database DSN", how)
        return 1
    os.environ["DATABASE_URL"] = dsn
    conn = await MOD.connect(dsn)
    if hasattr(MOD, "install_stubs"):
        MOD.install_stubs()
    pre = None
    try:
        await _call(MOD.teardown, conn)
        if hasattr(MOD, "counts"):
            pre = await _call(MOD.counts, conn)
        for n in pre_seed:
            await _call(getattr(MOD, n), conn)
        await _call(getattr(MOD, seed_name), conn)
        if pick:
            await _call(getattr(MOD, pick), conn)
        else:
            find(f"{name}: no fixture section after the seed in main's order — seed + teardown only")
    except Exception as exc:  # noqa: BLE001 — the step fails, the teardown still runs
        import traceback
        traceback.print_exc()
        check(False, "one-off step raised", f"{type(exc).__name__}: {exc}")
    finally:
        try:
            from services.database import close_pool
            await close_pool()
        except Exception:  # noqa: BLE001
            pass
        try:
            await _call(MOD.teardown, conn)
            if hasattr(MOD, "leftovers"):
                left = await _call(MOD.leftovers, conn)
                check(int(left) == 0, "one-off: zero fixture rows remain", f"{left}")
            if pre is not None:
                post = await _call(MOD.counts, conn)
                moved = {k: (pre[k], post.get(k)) for k in pre if post.get(k) != pre[k]}
                check(not moved, "one-off: every counted table back at its pre-run count", f"{moved}")
        except Exception as exc:  # noqa: BLE001
            check(False, "one-off teardown raised", f"{type(exc).__name__}: {exc}")
        finally:
            await conn.close()
    p, f = _counts()
    print(VT.total_line(p, f))
    return 0 if f == 0 else 1


if __name__ == "__main__":
    VT.install_guard(_counts)
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    sec = next((a.split("=", 1)[1] for a in sys.argv[1:] if a.startswith("--section=")), None)
    if not args:
        print("usage: _verify_oneoff.py verify_x.py [--section=y_name]")
        print(VT.total_line(0, 1))
        sys.exit(2)
    sys.exit(asyncio.run(drive(args[0], sec)))
