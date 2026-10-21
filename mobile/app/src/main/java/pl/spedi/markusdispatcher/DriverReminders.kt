package pl.spedi.markusdispatcher

import android.Manifest
import android.annotation.SuppressLint
import android.app.AlarmManager
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.BroadcastReceiver
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.net.Uri
import android.os.Build
import androidx.core.app.NotificationCompat
import androidx.core.content.ContextCompat
import androidx.work.*
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.Source
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.TimeoutCancellationException
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import java.time.Instant
import java.util.concurrent.TimeUnit

object DriverReminders {
    const val CHANNEL = "driver_deadlines"
    private const val WORK = "driver_deadline_refresh"
    private const val PREFS = "driver_alarm_queue"
    private var generation = 0L
    @Synchronized fun generation() = generation
    private fun prefs(context: Context) = context.getSharedPreferences(PREFS, Context.MODE_PRIVATE)
    private fun queue(context: Context): List<PhoneReminder> = runCatching {
        val array = JSONArray(prefs(context).getString("queue", "[]"))
        (0 until array.length()).map { PhoneReminder.fromJson(array.getJSONObject(it)) }
    }.getOrDefault(emptyList())

    fun createChannel(context: Context) {
        context.getSystemService(NotificationManager::class.java).createNotificationChannel(
            NotificationChannel(CHANNEL, "Powroty i odpoczynki kierowców", NotificationManager.IMPORTANCE_HIGH))
    }
    fun exactAllowed(context: Context) = Build.VERSION.SDK_INT < 31 || context.getSystemService(AlarmManager::class.java).canScheduleExactAlarms()

    @Synchronized fun activate(context: Context, uid: String) {
        if (uid.isBlank() || FirebaseAuth.getInstance().currentUser?.uid != uid) { clear(context); return }
        if (prefs(context).getString("uid", null) != uid) {
            clear(context)
            check(prefs(context).edit().putString("uid", uid).commit())
        }
        createChannel(context)
        val work = PeriodicWorkRequestBuilder<DriverRefreshWorker>(1, TimeUnit.HOURS)
            .setConstraints(Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build())
            .setInputData(workDataOf("uid" to uid)).build()
        WorkManager.getInstance(context).enqueueUniquePeriodicWork(WORK, ExistingPeriodicWorkPolicy.UPDATE, work)
        restore(context)
    }

    @Synchronized fun update(context: Context, uid: String, sources: List<DriverSchedule>, expectedGeneration: Long? = null) {
        if (FirebaseAuth.getInstance().currentUser?.uid != uid || prefs(context).getString("uid", null) != uid) return
        if (expectedGeneration != null && expectedGeneration != generation) return
        val old = queue(context)
        val store = DriverPreferenceStore(context)
        val desired = ReminderPolicy.desired(uid, sources) { store.read(uid, it) }
        val ids = desired.map { it.id }.toSet()
        val previousDelivered = prefs(context).getStringSet("delivered", emptySet()).orEmpty()
        val delivered = previousDelivered.intersect(ids)
        val updated = ReminderPolicy.reconcile(old, desired, delivered, Instant.now())
        check(prefs(context).edit().putString("queue", JSONArray(updated.map { it.toJson() }).toString())
            .putStringSet("delivered", delivered).commit())
        generation++
        context.getSystemService(NotificationManager::class.java).let { manager ->
            old.filter { it.id !in ids }.forEach { manager.cancel(it.id, 0) }
            (previousDelivered - ids).forEach { manager.cancel(it, 0) }
        }
        arm(context)
    }

    private fun alarmIntent(context: Context) = PendingIntent.getBroadcast(context, 0,
        Intent(context, DriverAlarmReceiver::class.java).setAction("pl.spedi.DRIVER_DEADLINES")
            .setData(Uri.parse("spedi://driver-deadlines")), PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)

    @SuppressLint("ScheduleExactAlarm")
    private fun arm(context: Context) {
        val manager = context.getSystemService(AlarmManager::class.java)
        val intent = alarmIntent(context)
        manager.cancel(intent)
        val next = queue(context).minOfOrNull { it.due.toEpochMilli() } ?: return
        val trigger = maxOf(next, System.currentTimeMillis() + 1000)
        if (exactAllowed(context)) {
            try { manager.setExactAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger, intent); return }
            catch (_: SecurityException) { /* Permission changed between check and call. */ }
        }
        manager.setAndAllowWhileIdle(AlarmManager.RTC_WAKEUP, trigger, intent)
    }

    @Synchronized fun restore(context: Context) {
        val uid = prefs(context).getString("uid", null) ?: return
        if (FirebaseAuth.getInstance().currentUser?.uid != uid) { clear(context); return }
        val old = queue(context)
        val retained = ReminderPolicy.reconcile(old, old, prefs(context).getStringSet("delivered", emptySet()).orEmpty(), Instant.now())
        check(prefs(context).edit().putString("queue", JSONArray(retained.map { it.toJson() }).toString()).commit())
        arm(context)
    }

    @Synchronized fun deliver(context: Context) {
        val uid = prefs(context).getString("uid", null) ?: return
        if (FirebaseAuth.getInstance().currentUser?.uid != uid) { clear(context); return }
        val now = Instant.now()
        val old = queue(context)
        createChannel(context)
        ReminderPolicy.ready(old, now).forEach { notify(context, uid, it) }
        val processed = old.filter { !it.due.isAfter(now) }.map { it.id }
        val delivered = prefs(context).getStringSet("delivered", emptySet()).orEmpty() + processed
        check(prefs(context).edit().putString("queue", JSONArray(old.filter { it.due.isAfter(now) }.map { it.toJson() }).toString())
            .putStringSet("delivered", delivered).commit())
        generation++
        arm(context)
    }

    private fun notify(context: Context, uid: String, reminder: PhoneReminder) {
        if (DriverPreferenceStore(context).read(uid, reminder.driverId).hidden) return
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
        if (!context.getSystemService(NotificationManager::class.java).areNotificationsEnabled()) return
        val intent = Intent(context, MainActivity::class.java).apply {
            flags = Intent.FLAG_ACTIVITY_CLEAR_TOP or Intent.FLAG_ACTIVITY_SINGLE_TOP
            data = Uri.parse("spedi://driver/${reminder.id}")
            putExtra("uid", uid); putExtra("driver_id", reminder.driverId)
        }
        val open = PendingIntent.getActivity(context, 0, intent, PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE)
        val body = "${reminder.driverName} · ${reminder.title}\nTermin: ${formatDriverInstant(reminder.deadline)} · przypomnienie ${reminder.lead} h wcześniej"
        val notification = NotificationCompat.Builder(context, CHANNEL).setSmallIcon(R.drawable.ic_notification)
            .setContentTitle(reminder.driverName).setContentText(body).setStyle(NotificationCompat.BigTextStyle().bigText(body))
            .setContentIntent(open).setAutoCancel(true).setOnlyAlertOnce(true).build()
        try { context.getSystemService(NotificationManager::class.java).notify(reminder.id, 0, notification) }
        catch (_: SecurityException) { /* Permission revoked during delivery. */ }
    }

    @Synchronized fun clear(context: Context) {
        generation++
        context.getSystemService(AlarmManager::class.java).cancel(alarmIntent(context))
        check(prefs(context).edit().clear().commit())
        WorkManager.getInstance(context).cancelUniqueWork(WORK)
        context.getSystemService(NotificationManager::class.java).cancelAll()
    }
}

class DriverAlarmReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action == "pl.spedi.DRIVER_DEADLINES") DriverReminders.deliver(context)
    }
}

class DriverReminderRestoreReceiver : BroadcastReceiver() {
    override fun onReceive(context: Context, intent: Intent) {
        if (intent.action in setOf(Intent.ACTION_BOOT_COMPLETED, Intent.ACTION_MY_PACKAGE_REPLACED,
            Intent.ACTION_TIME_CHANGED, Intent.ACTION_TIMEZONE_CHANGED,
            AlarmManager.ACTION_SCHEDULE_EXACT_ALARM_PERMISSION_STATE_CHANGED)) DriverReminders.restore(context)
    }
}

class DriverRefreshWorker(context: Context, params: WorkerParameters) : CoroutineWorker(context, params) {
    override suspend fun doWork(): Result {
        val uid = inputData.getString("uid") ?: return Result.success()
        if (FirebaseAuth.getInstance().currentUser?.uid != uid) return Result.success()
        val generation = DriverReminders.generation()
        return try {
            val snap = withTimeout(30_000) { FirebaseFirestore.getInstance().collection("users").document(uid)
                .collection("driver_schedules").get(Source.SERVER).await() }
            val drivers = snap.documents.filter { it.getBoolean("active") != false }.map { DriverSchedule.fromMap(it.id, it.data.orEmpty()) }
            DriverReminders.update(applicationContext, uid, drivers, generation)
            Result.success()
        } catch (_: TimeoutCancellationException) { Result.retry() }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { Result.retry() }
    }
}
