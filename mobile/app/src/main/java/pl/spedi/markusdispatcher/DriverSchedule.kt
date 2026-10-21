package pl.spedi.markusdispatcher

import java.time.*
import java.time.format.DateTimeFormatter
import java.time.format.ResolverStyle
import java.util.Locale

val DriverZone: ZoneId = ZoneId.of("Europe/Warsaw")
private val editFormatter = DateTimeFormatter.ofPattern("dd.MM.uuuu HH:mm", Locale.ROOT).withResolverStyle(ResolverStyle.STRICT)
private val dateFormatter = DateTimeFormatter.ofPattern("dd.MM.uuuu", Locale.ROOT).withResolverStyle(ResolverStyle.STRICT)

data class DriverEvent(val key: String, val driverId: String, val driverName: String, val title: String, val at: Instant)
data class Compensation(val minutes: Long, val due: Instant?, val latestStart: Instant?, val mobilityPair: Boolean)
data class DriverSchedule(
    val id: String, val name: String, val revision: String, val week1: LocalDate?,
    val week4: LocalDate?, val week4Start: Instant?, val departure: Instant?, val anchor: Instant?,
    val plannedReturn: Instant?, val restStartBy: Instant?, val nextWeeklyRest: Instant?,
    val compensations: List<Compensation>, val findings: List<String>, val lastActivity: Instant?,
    val syncedAt: Instant?, val conflict: Boolean, val pendingPlan: Map<String, Any?>?,
    val localEdited: Boolean = false, val sourceRestStartBy: Instant? = restStartBy,
) {
    fun events(): List<DriverEvent> = listOfNotNull(
        plannedReturn?.let { DriverEvent("$id-return", id, name, "Powrót planowany", it) },
        week4Start?.let { DriverEvent("$id-week4", id, name, "Start tygodnia 4", it) },
        restStartBy?.let { DriverEvent("$id-rest", id, name, "Odpoczynek tygodniowy z rekompensatami", it) },
    ) + compensations.mapNotNull { c ->
        if (c.mobilityPair) null else c.due?.let { DriverEvent("$id-comp-${it}", id, name, "Termin rekompensaty", it) }
    }

    fun tripLabel(now: Instant): String {
        val start = departure ?: anchor ?: return "Start wyjazdu nieuzupełniony"
        val duration = Duration.between(start, now)
        return if (duration.isNegative) "Wyjazd: ${formatDriverInstant(start)}"
        else "${if (departure != null) "W trasie" else "Od oznaczonego startu"} ${duration.toDays()} dni ${duration.toHours() % 24} h"
    }

    companion object {
        fun fromMap(id: String, d: Map<String, Any?>): DriverSchedule {
            fun instant(key: String) = parseDriverInstant(d[key]?.toString())
            fun date(key: String) = runCatching { LocalDate.parse(d[key]?.toString()) }.getOrNull()
            val comps = (d["pending_compensations"] as? List<*>)?.mapNotNull { item ->
                val c = item as? Map<*, *> ?: return@mapNotNull null
                Compensation((c["remaining_minutes"] as? Number)?.toLong() ?: 0,
                    parseDriverInstant(c["compensation_due_at"]?.toString()),
                    parseDriverInstant(c["latest_compensation_start_at"]?.toString()), c["mobility_pair"] == true)
            }.orEmpty()
            val findings = (d["findings"] as? List<*>)?.mapNotNull { item ->
                val f = item as? Map<*, *> ?: return@mapNotNull null
                if (f["severity"] == "ok") null else f["message"]?.toString()
            }.orEmpty()
            @Suppress("UNCHECKED_CAST")
            val pending = d["pending_plan"] as? Map<String, Any?>
            val timestamp = d["synced_at"] as? com.google.firebase.Timestamp
            return DriverSchedule(id, d["display_name"]?.toString().orEmpty(), d["revision"]?.toString().orEmpty(),
                date("week1_monday"), date("week4_monday"), instant("week4_start_at"), instant("departure_at"),
                instant("anchor_at"), instant("planned_return_at"), instant("rest_start_by"), instant("next_weekly_rest_start_by"),
                comps, findings, instant("last_activity_at"), timestamp?.toDate()?.toInstant(), d["sync_conflict"] == true, pending)
        }
    }
}

fun parseDriverInstant(value: String?): Instant? = runCatching { Instant.parse(value) }.getOrNull()
fun formatDriverInstant(value: Instant?): String = value?.atZone(DriverZone)?.format(editFormatter) ?: "Nie ustalono"
fun formatDriverDate(value: LocalDate?): String = value?.format(dateFormatter) ?: "Nie oznaczono"

fun parseWeek1(value: String): String? {
    if (value.isBlank()) return null
    val date = LocalDate.parse(value.trim(), dateFormatter)
    require(date.dayOfWeek == DayOfWeek.MONDAY) { "Start tygodnia 1 musi być poniedziałkiem." }
    return date.toString()
}

fun parsePlanTime(value: String, occurrence: Int? = null): String? {
    if (value.isBlank()) return null
    val local = LocalDateTime.parse(value.trim(), editFormatter)
    val offsets = DriverZone.rules.getValidOffsets(local)
    require(offsets.isNotEmpty()) { "Ta godzina nie istnieje przy zmianie czasu. Wybierz inną." }
    require(offsets.size == 1 || occurrence in 0..1) { "Godzina występuje dwukrotnie. Wybierz wariant zmiany czasu poniżej." }
    return local.toInstant(if (offsets.size == 2) offsets[occurrence!!] else offsets[0]).toString()
}

fun buildDriverPlan(week1: String, departure: String, plannedReturn: String, departureOccurrence: Int?,
                    returnOccurrence: Int? = departureOccurrence, originalDeparture: Instant? = null,
                    originalReturn: Instant? = null): Map<String, String?> {
    val start = if (originalDeparture != null && departure == formatDriverInstant(originalDeparture)) originalDeparture.toString() else parsePlanTime(departure, departureOccurrence)
    val end = if (originalReturn != null && plannedReturn == formatDriverInstant(originalReturn)) originalReturn.toString() else parsePlanTime(plannedReturn, returnOccurrence)
    require(start == null || end == null || !Instant.parse(end).isBefore(Instant.parse(start))) { "Powrót nie może być przed wyjazdem." }
    return mapOf("week1_monday" to parseWeek1(week1), "departure_at" to start, "planned_return_at" to end)
}
