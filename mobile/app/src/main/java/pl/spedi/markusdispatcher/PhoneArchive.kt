package pl.spedi.markusdispatcher

import android.content.Context
import androidx.datastore.core.DataStore
import androidx.datastore.preferences.core.Preferences
import androidx.datastore.preferences.core.edit
import androidx.datastore.preferences.core.stringPreferencesKey
import androidx.datastore.preferences.preferencesDataStore
import kotlinx.coroutines.flow.map
import org.json.JSONArray
import org.json.JSONObject

private val Context.phoneArchive by preferencesDataStore(name = "phone_archive")

object ArchiveCodec {
    fun encode(records: Map<String, HiddenJob>): String = JSONArray().apply {
        records.values.forEach { record -> put(JSONObject().apply {
            put("jobId", record.jobId); put("status", record.status)
            put("reason", record.reason); put("reference", record.reference)
        }) }
    }.toString()

    fun decode(raw: String): Map<String, HiddenJob> {
        val array = JSONArray(raw)
        return (0 until array.length()).map { index -> array.getJSONObject(index).let {
            HiddenJob(it.getString("jobId"), it.getString("status"), it.getString("reason"), it.optString("reference", it.getString("jobId")))
        } }.associateBy { it.jobId }
    }
}

class PhoneArchive(private val store: DataStore<Preferences>) {
    constructor(context: Context) : this(context.applicationContext.phoneArchive)
    private fun key(uid: String) = stringPreferencesKey("account:$uid")
    fun observe(uid: String) = store.data.map { ArchiveCodec.decode(it[key(uid)] ?: "[]") }
    suspend fun hide(uid: String, job: JobDto) { store.edit { prefs ->
        val records = ArchiveCodec.decode(prefs[key(uid)] ?: "[]").toMutableMap()
        records[job.id] = HiddenJob(job.id, job.status, reference = job.refNumbers.ifBlank { job.id })
        prefs[key(uid)] = ArchiveCodec.encode(records)
    } }
    suspend fun restore(uid: String, ids: Set<String>) { store.edit { prefs ->
        prefs[key(uid)] = ArchiveCodec.encode(ArchiveCodec.decode(prefs[key(uid)] ?: "[]") - ids)
    } }
    // Evaluate against the latest records inside the transaction: a concurrent re-hide is safe.
    suspend fun reconcile(uid: String, jobs: List<JobDto>) { store.edit { prefs ->
        val records = ArchiveCodec.decode(prefs[key(uid)] ?: "[]")
        val statuses = jobs.associate { it.id to it.status }
        val next = records.filterValues { !ArchiveRules.shouldRestore(it, statuses[it.jobId].orEmpty()) }
        if (next != records) prefs[key(uid)] = ArchiveCodec.encode(next)
    } }
}
