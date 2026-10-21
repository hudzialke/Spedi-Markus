package pl.spedi.markusdispatcher

import android.content.Context
import org.json.JSONArray
import org.json.JSONObject
import java.time.LocalDate
import java.time.Instant

val ReminderLeadHours = listOf(72, 48, 24)
val PhonePlanFields = setOf("week1_monday", "departure_at", "planned_return_at", "rest_start_by")

data class DriverPreferences(
    val overrides: Map<String, String?> = emptyMap(),
    val leads: Set<Int> = ReminderLeadHours.toSet(),
    val hidden: Boolean = false,
) {
    fun applyTo(source: DriverSchedule): DriverSchedule {
        fun instant(key: String, fallback: Instant?) = if (overrides.containsKey(key)) parseDriverInstant(overrides[key]) else fallback
        val week1 = if (overrides.containsKey("week1_monday")) overrides["week1_monday"]?.let(LocalDate::parse) else source.week1
        val week4 = if (overrides.containsKey("week1_monday")) week1?.plusWeeks(3) else source.week4
        return source.copy(week1 = week1, week4 = week4,
            week4Start = if (overrides.containsKey("week1_monday")) week4?.atStartOfDay(DriverZone)?.toInstant() else source.week4Start,
            departure = instant("departure_at", source.departure), plannedReturn = instant("planned_return_at", source.plannedReturn),
            restStartBy = instant("rest_start_by", source.restStartBy), localEdited = overrides.isNotEmpty(),
            sourceRestStartBy = source.restStartBy)
    }

    fun toJson(): JSONObject = JSONObject().put("overrides", JSONObject().apply {
        overrides.forEach { (k, v) -> put(k, v ?: JSONObject.NULL) }
    }).put("leads", JSONArray(leads.sortedDescending())).put("hidden", hidden)

    companion object {
        fun fromJson(json: JSONObject): DriverPreferences {
            val obj = json.optJSONObject("overrides") ?: JSONObject()
            val values = PhonePlanFields.filter(obj::has).associateWith { if (obj.isNull(it)) null else obj.getString(it) }
            val leads = json.optJSONArray("leads")
            return DriverPreferences(values, if (leads == null) ReminderLeadHours.toSet()
                else (0 until leads.length()).map { leads.getInt(it) }.filter { it in ReminderLeadHours }.toSet(),
                hidden = json.optBoolean("hidden", false))
        }
    }
}

// Preserve existing overrides, while allowing unchanged fields to follow PC updates.
fun changedPhonePlan(source: DriverSchedule, current: DriverPreferences, values: Map<String, String?>): Map<String, String?> {
    val baseline = mapOf("week1_monday" to source.week1?.toString(), "departure_at" to source.departure?.toString(),
        "planned_return_at" to source.plannedReturn?.toString(), "rest_start_by" to source.restStartBy?.toString())
    return values.filter { (key, value) -> key in PhonePlanFields && (key in current.overrides || value != baseline[key]) }
}

class DriverPreferenceStore(context: Context) {
    private val prefs = context.getSharedPreferences("driver_phone_plans", Context.MODE_PRIVATE)
    private fun key(uid: String, id: String) = "$uid/$id"
    fun read(uid: String, id: String): DriverPreferences = runCatching {
        DriverPreferences.fromJson(JSONObject(prefs.getString(key(uid, id), "{}")!!))
    }.getOrDefault(DriverPreferences())
    fun save(uid: String, id: String, value: DriverPreferences) {
        check(prefs.edit().putString(key(uid, id), value.toJson().toString()).commit()) { "Nie zapisano danych w telefonie." }
    }
}
