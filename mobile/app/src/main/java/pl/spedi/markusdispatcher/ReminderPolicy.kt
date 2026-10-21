package pl.spedi.markusdispatcher

import org.json.JSONObject
import java.time.Duration
import java.time.Instant
import java.security.MessageDigest

data class PhoneReminder(val id: String, val driverId: String, val driverName: String,
                         val title: String, val deadline: Instant, val due: Instant, val lead: Int) {
    fun toJson(): JSONObject = JSONObject().put("id", id).put("driverId", driverId).put("driverName", driverName)
        .put("title", title).put("deadline", deadline.toString()).put("due", due.toString()).put("lead", lead)
    companion object {
        fun fromJson(o: JSONObject) = PhoneReminder(o.getString("id"), o.getString("driverId"), o.getString("driverName"),
            o.getString("title"), Instant.parse(o.getString("deadline")), Instant.parse(o.getString("due")), o.getInt("lead"))
    }
}

object ReminderPolicy {
    private val grace = Duration.ofHours(6)
    fun desired(uid: String, drivers: List<DriverSchedule>, preferences: (String) -> DriverPreferences): List<PhoneReminder> =
        drivers.flatMap { source ->
            val pref = preferences(source.id)
            if (pref.hidden) return@flatMap emptyList()
            pref.applyTo(source).events().distinctBy { it.key }.flatMap { event ->
                pref.leads.filter { it in ReminderLeadHours }.map { lead ->
                    val identity = "$uid|${event.key}|${event.at}|$lead"
                    val id = MessageDigest.getInstance("SHA-256").digest(identity.toByteArray()).joinToString("") { "%02x".format(it) }
                    PhoneReminder(id, source.id, source.name, event.title, event.at, event.at.minus(Duration.ofHours(lead.toLong())), lead)
                }
            }
        }

    // Newly downloaded historical reminders never fire. Retain missed scheduled ones for a short catch-up after reboot.
    fun reconcile(previous: List<PhoneReminder>, desired: List<PhoneReminder>, delivered: Set<String>, now: Instant): List<PhoneReminder> {
        val scheduled = previous.map { it.id }.toSet()
        return desired.filter { it.id !in delivered && (!it.due.isBefore(now) ||
            (it.id in scheduled && Duration.between(it.due, now) <= grace)) }.sortedBy { it.due }
    }

    fun ready(queue: List<PhoneReminder>, now: Instant): List<PhoneReminder> = queue
        .filter { !it.due.isAfter(now) && Duration.between(it.due, now) <= grace }
        .groupBy { "${it.driverId}|${it.title}|${it.deadline}" }.values.map { group -> group.maxBy { it.due } }
}
