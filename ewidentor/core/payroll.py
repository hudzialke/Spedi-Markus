from __future__ import annotations

from dataclasses import dataclass, field

from ewidentor.core.holidays_pl import norma_kp_minutes
from ewidentor.core.models import AbsenceKind, Driver, MonthCard, PayrollBreakdown
from ewidentor.core.month_card import sum_field


@dataclass(slots=True)
class PayrollLine:
    section: str  # A | B | C | TOTAL | NET
    label: str
    days: float | None = None
    minutes: int = 0
    rate: float = 0.0
    amount: float = 0.0
    note: str = ""


@dataclass(slots=True)
class PayrollResult:
    norma_min: int
    norma_formula: str
    monthly_salary: float
    hourly_rate: float
    night_rate: float
    duty_50_rate: float
    work_min: int
    night_min: int
    duty_50_min: int
    duty_100_min: int
    sunday_unreturned_min: int
    ot_returned_no_request_min: int
    unpaid_leave_min: int = 0
    wymiar_min: int = 0
    ot_50_base_min: int = 0
    over_plan_min: int = 0
    lines: list[PayrollLine] = field(default_factory=list)
    section_a: float = 0.0
    section_b: float = 0.0
    section_c: float = 0.0
    milo_topup_pln: float = 0.0
    gross_total: float = 0.0
    net_estimate: float = 0.0
    # compat aliases for starszy kod / PDF
    paid_min: int = 0
    overtime_min: int = 0
    base_pay: float = 0.0
    night_pay: float = 0.0
    duty_pay: float = 0.0
    overtime_pay: float = 0.0
    duty_rate: float = 0.0


def estimate_net_pl(gross: float, *, tax_reducing: bool = True) -> float:
    """
    Uproszczone netto PL (orientacyjne, nie zastępuje listy płac).
    - ZUS pracownik ~13,71%
    - zdrowotna 9% od (brutto − ZUS)
    - PIT 12% od (brutto − ZUS − KUP 250), minus 300 zł ulgi mies. (gdy tax_reducing)
    """
    if gross <= 0:
        return 0.0
    zus = gross * 0.1371
    health_base = max(0.0, gross - zus)
    health = health_base * 0.09
    kup = 250.0
    tax_base = max(0.0, gross - zus - kup)
    pit = tax_base * 0.12
    if tax_reducing:
        pit = max(0.0, pit - 300.0)
    net = gross - zus - health - pit
    return round(max(0.0, net), 2)


def compute_payroll_detailed(
    card: MonthCard,
    *,
    ot_returned_no_request_min: int = 0,
    ot_returned_supplement_rate: float | None = None,
    sunday_unreturned_min: int | None = None,
    milo_topup_pln: float = 0.0,
) -> PayrollResult:
    """
    Przeliczenie jak wzorzec „Przeliczenie miesięcznej karty”:
    A zasadnicze (+ uzupełnienie oddanych nadg. bez wniosku)
    B noc + dyżur 50%
    C nieoddane niedziele/święta 100%
    + szacunek netto PL
    """
    driver: Driver = card.driver
    pay = driver.pay

    norma_min, norma_meta = norma_kp_minutes(
        card.year, card.month, employment_fraction=driver.employment_fraction
    )
    norma_min = norma_min or card.norma_min or 1

    hourly = pay.hourly_rate if pay.hourly_rate is not None else (
        pay.monthly_salary / (norma_min / 60.0) if norma_min else 0.0
    )
    hourly = round(hourly, 2)
    night_rate = round(hourly * (pay.night_percent / 100.0), 2)
    duty_50_rate = round(hourly * (pay.duty_percent / 100.0), 2)
    ot_50_rate = round(hourly * (pay.overtime_50_percent / 100.0), 2)

    work_min = sum_field(card.days, "work_min")
    night_min = sum_field(card.days, "night_min")
    duty_50 = sum_field(card.days, "duty_50_min")
    duty_100 = sum_field(card.days, "duty_100_min")
    paid_min = sum_field(card.days, "paid_min")

    fte = driver.employment_fraction or 1.0
    unpaid_leave_min = 0
    unpaid_days = 0
    for d in card.days:
        if d.absence_kind != AbsenceKind.UNPAID_LEAVE:
            continue
        unpaid_days += 1
        unpaid_leave_min += d.plan_min if d.plan_min > 0 else int(8 * 60 * fte)
    wymiar_min = max(0, norma_min - unpaid_leave_min)

    # Nadgodziny dobowe vs plan dnia (nie vs 160 h).
    ot_50_base_min = 0
    for d in card.days:
        if d.absence_kind == AbsenceKind.UNPAID_LEAVE:
            continue
        if d.plan_min > 0 and d.work_min > d.plan_min:
            ot_50_base_min += d.work_min - d.plan_min
    over_plan_min = max(0, work_min - ot_50_base_min - wymiar_min)
    overtime = ot_50_base_min + over_plan_min

    if sunday_unreturned_min is None:
        sunday_unreturned_min = sum_field(card.days, "sunday_holiday_min")

    if ot_returned_supplement_rate is None:
        ot_returned_supplement_rate = round(hourly * 0.05, 2)

    lines: list[PayrollLine] = []

    lines.append(
        PayrollLine(
            section="A",
            label="Stawka miesięczna",
            amount=round(pay.monthly_salary, 2),
        )
    )
    unpaid_deduction = 0.0
    if unpaid_leave_min > 0 and hourly > 0:
        unpaid_deduction = round((unpaid_leave_min / 60.0) * hourly, 2)
        lines.append(
            PayrollLine(
                section="A",
                label="Odliczenie za urlop bezpłatny (na pisemny wniosek)",
                days=float(unpaid_days),
                minutes=unpaid_leave_min,
                rate=hourly,
                amount=-unpaid_deduction,
            )
        )
    ot_supp = 0.0
    if ot_returned_no_request_min > 0 and ot_returned_supplement_rate > 0:
        ot_supp = round(
            (ot_returned_no_request_min / 60.0) * ot_returned_supplement_rate, 2
        )
        lines.append(
            PayrollLine(
                section="A",
                label="Uzupełnienie wynagrodzenia z tyt. oddanych nadg. bez wniosku",
                minutes=ot_returned_no_request_min,
                rate=ot_returned_supplement_rate,
                amount=ot_supp,
            )
        )

    # Nadgodziny: stawka normalna + dodatek 50% za przekroczenie planu dnia.
    # „Ponad plan” vs wymiar po urlopie. Dyżur nie wchodzi do bazy OT.
    overtime_pay = 0.0
    if ot_50_base_min > 0 and hourly > 0:
        normal_amt = round((ot_50_base_min / 60.0) * hourly, 2)
        addon_amt = round((ot_50_base_min / 60.0) * ot_50_rate, 2)
        overtime_pay += normal_amt + addon_amt
        lines.append(
            PayrollLine(
                section="B",
                label="Wynagrodzenie normalne za godz. nadliczbowe 50%",
                minutes=ot_50_base_min,
                rate=hourly,
                amount=normal_amt,
                note="powyżej planu dnia; dyżur nie wchodzi do bazy OT",
            )
        )
        lines.append(
            PayrollLine(
                section="B",
                label=f"Dodatki za godz. nadliczbowe {pay.overtime_50_percent:.0f}%",
                minutes=ot_50_base_min,
                rate=ot_50_rate,
                amount=addon_amt,
            )
        )
    if over_plan_min > 0 and hourly > 0:
        amt = round((over_plan_min / 60.0) * hourly, 2)
        overtime_pay += amt
        lines.append(
            PayrollLine(
                section="B",
                label="Wynagrodzenie za czas płatny ponad plan",
                minutes=over_plan_min,
                rate=hourly,
                amount=amt,
                note="praca − OT dobowe − wymiar po urlopie",
            )
        )
    overtime_pay = round(overtime_pay, 2)
    section_a = round(pay.monthly_salary - unpaid_deduction + ot_supp, 2)

    night_pay = round((night_min / 60.0) * night_rate, 2)
    lines.append(
        PayrollLine(
            section="B",
            label=f"Dodatki za czas pracy w godz. nocnych ({pay.night_percent:.0f}%)",
            minutes=night_min,
            rate=night_rate,
            amount=night_pay,
        )
    )
    duty_pay = round((duty_50 / 60.0) * duty_50_rate, 2)
    lines.append(
        PayrollLine(
            section="B",
            label=f"Wynagrodzenie za czas dyżurów {pay.duty_percent:.0f}%",
            minutes=duty_50,
            rate=duty_50_rate,
            amount=duty_pay,
            note=f"{pay.duty_percent:.0f}% × {hourly:.2f} zł",
        )
    )
    duty_100_pay = round((duty_100 / 60.0) * hourly, 2)
    if duty_100 > 0:
        lines.append(
            PayrollLine(
                section="B",
                label="Wynagrodzenie za czas dyżurów 100%",
                minutes=duty_100,
                rate=hourly,
                amount=duty_100_pay,
            )
        )
    section_b = round(night_pay + duty_pay + duty_100_pay + overtime_pay, 2)

    sunday_pay = round((sunday_unreturned_min / 60.0) * hourly, 2)
    lines.append(
        PayrollLine(
            section="C",
            label="Dodatki za pracę w nieoddane niedziele i święta (100%)",
            minutes=sunday_unreturned_min,
            rate=hourly,
            amount=sunday_pay,
        )
    )
    section_c = sunday_pay

    milo = round(max(0.0, milo_topup_pln), 2)
    if milo > 0:
        lines.append(
            PayrollLine(
                section="D",
                label="Wyrównanie MiLo (top-up)",
                minutes=0,
                rate=0.0,
                amount=milo,
            )
        )

    gross = round(section_a + section_b + section_c + milo, 2)
    net = estimate_net_pl(gross)

    return PayrollResult(
        norma_min=norma_min,
        norma_formula=str(norma_meta["formula"]),
        monthly_salary=pay.monthly_salary,
        hourly_rate=hourly,
        night_rate=night_rate,
        duty_50_rate=duty_50_rate,
        work_min=work_min,
        night_min=night_min,
        duty_50_min=duty_50,
        duty_100_min=duty_100,
        sunday_unreturned_min=sunday_unreturned_min,
        ot_returned_no_request_min=ot_returned_no_request_min,
        unpaid_leave_min=unpaid_leave_min,
        wymiar_min=wymiar_min,
        ot_50_base_min=ot_50_base_min,
        over_plan_min=over_plan_min,
        lines=lines,
        section_a=section_a,
        section_b=section_b,
        section_c=section_c,
        milo_topup_pln=milo,
        gross_total=gross,
        net_estimate=net,
        paid_min=paid_min,
        overtime_min=overtime,
        base_pay=section_a,
        night_pay=night_pay,
        duty_pay=duty_pay + duty_100_pay,
        overtime_pay=overtime_pay + ot_supp,
        duty_rate=duty_50_rate,
    )


def to_legacy_breakdown(result: PayrollResult) -> PayrollBreakdown:
    return PayrollBreakdown(
        norma_min=result.norma_min,
        monthly_salary=result.monthly_salary,
        hourly_rate=result.hourly_rate,
        night_rate=result.night_rate,
        duty_rate=result.duty_rate,
        work_min=result.work_min,
        paid_min=result.paid_min,
        night_min=result.night_min,
        duty_50_min=result.duty_50_min,
        duty_100_min=result.duty_100_min,
        overtime_min=result.overtime_min,
        base_pay=result.base_pay,
        night_pay=result.night_pay,
        duty_pay=result.duty_pay,
        overtime_pay=result.overtime_pay,
        gross_total=result.gross_total,
        net_estimate=result.net_estimate,
        norma_formula=result.norma_formula,
        sunday_unreturned_min=result.sunday_unreturned_min,
        section_a=result.section_a,
        section_b=result.section_b,
        section_c=result.section_c,
        milo_topup_pln=result.milo_topup_pln,
    )


def compute_payroll(card: MonthCard, **kwargs) -> PayrollBreakdown:
    """API dla UI — PayrollBreakdown (kompatybilne)."""
    return to_legacy_breakdown(compute_payroll_detailed(card, **kwargs))
