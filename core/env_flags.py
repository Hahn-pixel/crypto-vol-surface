#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
[EN] Single source of truth for environment-flag handling. Every
VOLEDGE_* variable a script reads must be declared; an unknown name
(e.g. a typo) prints [ENV-WARN] instead of silently falling back to a
default.

--- Ukrainian original below ---
Vol-Edge :: core/env_flags.py — ЄДИНЕ ДЖЕРЕЛО [ENV-WARN].

ЧОМУ ОКРЕМИЙ МОДУЛЬ, А НЕ КОПІЯ В КОЖНОМУ ФАЙЛІ
------------------------------------------------
Нерозпізнана змінна оточення = тихий дефолт. 22 сер це коштувало двох
зайвих прогонів бекфілу: обгортка експортувала VOLEDGE_RVROLL_MODE, а
модуль читає VOLEDGE_ROLL_MODE — ні помилки, ні попередження.

Перша реалізація попередження була скопійована у два файли і за один
день РОЗІЙШЛАСЯ: у rv_rolling.py це inline-блок усередині main() без
параметра quiet, без повернення значення і принципово невикриваний
самотестом; у tenor_nodes.py — функція з quiet і двома тестами, з
іншим текстом друку. Копіювання такого коду ще у сім модулів дало б
вісім розбіжних попереджень. Тому канон один, тут.

ПРАВИЛО СКЛАДУ СПИСКУ (перевірено 24 сер, не припущення)
---------------------------------------------------------
У KNOWN_ENV точки входу входить ЛИШЕ те, що вона фактично читає під
час свого прогону. Успадковувати список імпортованого модуля можна
тільки за ДОВЕДЕНИМ фактом, що той читає оточення в коді, який реально
виконується.

Гіпотеза «калібратор мусить успадкувати список детектора, бо інакше
VOLEDGE_RANK_K дасть хибне попередження» БУЛА ПЕРЕВІРЕНА І ВІДХИЛЕНА:
всі сім os.environ.get детектора сидять у його main(), а калібратор
імпортує лише чисті функції (features_of_snapshot, vrp_from_rv,
robust_z, list_artifacts, _load_json) — main() детектора не
виконується. Тобто VOLEDGE_RANK_K під час калібрувального прогону
справді нічого не робить (калібратор керується VOLEDGE_CALIB_RANK_K),
і попередження на неї ПРАВИЛЬНЕ. Сплутати RANK_K з CALIB_RANK_K так
само легко, як RVROLL_MODE з ROLL_MODE.

merge_known() лишається як механізм для випадку, коли імпортований
модуль читає оточення на рівні модуля або в бібліотечній функції. Для
пари детектор/калібратор він викликається з ОДНИМ списком. Розширювати
список «про всяк випадок» НЕ МОЖНА: кожне зайве ім'я — це рівно один
клас помилки, який попередження перестане ловити.

ХТО ВИКЛИКАЄ: лише точка входу (main), не бібліотечний код. Імпортований
як бібліотека модуль про повний склад змінних процесу не знає.

Чистий stdlib. Подвійний клік + input() завжди.
"""

import os
import sys
import traceback

PREFIX = "VOLEDGE_"

# Читається КОЖНИМ модулем проєкту — входить в об'єднання завжди.
UNIVERSAL_ENV = ("VOLEDGE_OFFLINE",)


def merge_known(*groups):
    """Об'єднання списків відомих змінних + UNIVERSAL_ENV.

    Приймає будь-яку кількість ітерованих; повертає відсортований tuple
    без дублікатів. Порожній виклик дає саме UNIVERSAL_ENV.
    """
    out = set(UNIVERSAL_ENV)
    for g in groups:
        if g is None:
            continue
        if isinstance(g, str):
            raise TypeError("merge_known приймає ітеровані імен, не рядок "
                            f"({g!r}) — інакше рядок розпадеться на літери")
        out.update(g)
    for name in out:
        if not name.startswith(PREFIX):
            raise ValueError(f"{name!r} не починається з {PREFIX!r} — "
                             f"такої змінної перевірка не побачить")
    return tuple(sorted(out))


def warn_unknown_env(known, environ=None, quiet=False, label=None):
    """[ENV-WARN] на кожну VOLEDGE_*, якої точка входу НЕ читає.

    known   — ітероване імен (зазвичай результат merge_known);
    environ — словник для тестів, за замовчуванням os.environ;
    quiet   — не друкувати (самотести не мусять смітити в живий лог,
              інакше в logs/ з'явиться хибне попередження про змінну,
              якої ніхто не встановлював);
    label   — ім'я точки входу для друку.

    Повертає відсортований список нерозпізнаних імен. Це ДІАГНОСТИКА,
    не блокування: змінна може призначатись сусідньому проєкту в тому
    самому crontab.
    """
    known = tuple(known)
    env = os.environ if environ is None else environ
    unknown = sorted(k for k in env
                     if k.startswith(PREFIX) and k not in known)
    if unknown and not quiet:
        who = f" ({label})" if label else ""
        for k in unknown:
            print(f"[ENV-WARN] {k}={env[k]!r} — ця точка входу{who} такої "
                  f"змінної НЕ читає; значення проігноровано.")
        print(f"[ENV-WARN] нерозпізнаних змінних: {len(unknown)}. "
              f"Читаються: {', '.join(known)}")
    return unknown


# ----------------------------------------------------------------------------
# Самотести
# ----------------------------------------------------------------------------

class TestCounters:
    def __init__(self):
        self.run = 0
        self.passed = 0
        self.failures = []

    def check(self, name, ok, detail=""):
        self.run += 1
        if ok:
            self.passed += 1
        else:
            self.failures.append(f"{name}: {detail}")
        print(f"[TEST] {'OK ' if ok else 'FAIL'} {name} {detail}")


def _raises(fn, exc):
    try:
        fn()
    except exc:
        return True
    except Exception:
        return False
    return False


def run_self_tests():
    tc = TestCounters()

    env = {"VOLEDGE_OFFLINE": "1", "VOLEDGE_TYPO_HERE": "1",
           "PATH": "/bin", "HOME": "/root"}

    unk = warn_unknown_env(("VOLEDGE_OFFLINE",), environ=env, quiet=True)
    tc.check("env_warn_flags_unknown", unk == ["VOLEDGE_TYPO_HERE"], str(unk))
    tc.check("env_warn_ignores_known", "VOLEDGE_OFFLINE" not in unk)
    tc.check("env_warn_ignores_non_voledge",
             "PATH" not in unk and "HOME" not in unk, str(unk))

    # Порожнє оточення -> порожній список, а не None.
    tc.check("env_warn_empty_is_list",
             warn_unknown_env(("VOLEDGE_OFFLINE",), environ={},
                              quiet=True) == [])

    # quiet=True мовчить. Без цього самотести модулів засмічували б
    # живий лог cron хибним попередженням про синтетичну змінну.
    import io
    import contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        warn_unknown_env(("VOLEDGE_OFFLINE",), environ=env, quiet=True)
    tc.check("env_warn_quiet_prints_nothing", buf.getvalue() == "",
             repr(buf.getvalue()[:60]))
    buf2 = io.StringIO()
    with contextlib.redirect_stdout(buf2):
        warn_unknown_env(("VOLEDGE_OFFLINE",), environ=env, quiet=False)
    tc.check("env_warn_loud_prints_name",
             "VOLEDGE_TYPO_HERE" in buf2.getvalue())

    # --- merge_known: та сама пастка, що з калібратором ---
    m = merge_known(("VOLEDGE_A",), ("VOLEDGE_B", "VOLEDGE_A"))
    tc.check("merge_dedups_and_sorts",
             m == ("VOLEDGE_A", "VOLEDGE_B", "VOLEDGE_OFFLINE"), str(m))
    tc.check("merge_always_has_universal",
             merge_known() == ("VOLEDGE_OFFLINE",), str(merge_known()))
    tc.check("merge_accepts_none", merge_known(None, ("VOLEDGE_A",))
             == ("VOLEDGE_A", "VOLEDGE_OFFLINE"))

    # Рядок замість списку розпався б на літери і зробив би перевірку
    # тотожно порожньою — тихий fail-open, тому TypeError.
    tc.check("merge_rejects_bare_string",
             _raises(lambda: merge_known("VOLEDGE_A"), TypeError))
    # Ім'я без префікса ніколи не спрацює як «відоме» — краще впасти
    # при старті, ніж вічно попереджати про робочу змінну.
    tc.check("merge_rejects_bad_prefix",
             _raises(lambda: merge_known(("RANK_K",)), ValueError))

    # Об'єднання глушить рівно те, що в нього поклали, і НЕ глушить
    # друкарську помилку поруч. Механізм на випадок імпортованого
    # модуля, який читає оточення на рівні модуля.
    own = ("VOLEDGE_CALIB_WINDOW",)
    imported = ("VOLEDGE_RANK_K", "VOLEDGE_COOLDOWN")
    live = {"VOLEDGE_RANK_K": "1", "VOLEDGE_CALIB_WINDOW": "60",
            "VOLEDGE_CALIB_WINDOWW": "60"}
    unk2 = warn_unknown_env(merge_known(own, imported), environ=live,
                            quiet=True)
    tc.check("merge_union_silences_listed",
             unk2 == ["VOLEDGE_CALIB_WINDOWW"], str(unk2))
    # Негативний контроль: без успадкування та сама змінна попадає в
    # нерозпізнані. Це НЕ дефект — саме така поведінка потрібна
    # калібратору, бо main() детектора під ним не виконується і
    # VOLEDGE_RANK_K там справді no-op. Тест фіксує, що розширення
    # списку має ціну: кожне зайве ім'я гасить один клас помилки.
    unk3 = warn_unknown_env(merge_known(own), environ=live, quiet=True)
    tc.check("narrow_list_still_catches_wrong_prefix_var",
             "VOLEDGE_RANK_K" in unk3, str(unk3))

    print(f"[TEST] passed {tc.passed}/{tc.run}")
    for f_ in tc.failures:
        print(f"[TEST] FAILURE detail: {f_}")
    return tc


def main() -> int:
    print("=" * 72)
    # Без літерала-маркера в заголовку: grep по логах мусить ловити
    # лише події (24 сер заголовок секції в deribit_chain дав хибну 1).
    print("Vol-Edge :: core/env_flags (канон попереджень про оточення)")
    print("=" * 72)
    tc = run_self_tests()
    print(f"[SUMMARY] самотести: {tc.run}, пройдено: {tc.passed}, "
          f"провалено: {len(tc.failures)}")
    return 1 if tc.failures else 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    except Exception:
        print("\n[ERROR] Неперехоплений виняток:")
        traceback.print_exc()
    finally:
        try:
            input("\nНатисніть Enter для виходу...")
        except EOFError:
            pass
    sys.exit(exit_code)
