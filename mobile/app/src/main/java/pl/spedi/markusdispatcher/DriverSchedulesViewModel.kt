package pl.spedi.markusdispatcher

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.ListenerRegistration
import com.google.firebase.firestore.MetadataChanges
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update


data class DriverSchedulesState(
    val uid: String = "", val drivers: List<DriverSchedule> = emptyList(), val loading: Boolean = true,
    val hiddenDrivers: List<DriverSchedule> = emptyList(),
    val fromCache: Boolean = true, val message: String = "", val saving: Boolean = false,
    val saveError: String = "", val savedDriverId: String? = null, val reminderMessage: String = "",
    val notificationsEnabled: Boolean = false, val exactAlarmsEnabled: Boolean = false,
)

class DriverSchedulesViewModel(application: Application) : AndroidViewModel(application) {
    private val auth = FirebaseAuth.getInstance()
    private val db = FirebaseFirestore.getInstance()
    private val preferences = DriverPreferenceStore(application)
    private var reg: ListenerRegistration? = null
    private var sources = emptyList<DriverSchedule>()
    private val _state = MutableStateFlow(DriverSchedulesState())
    val state = _state.asStateFlow()
    private val authListener = FirebaseAuth.AuthStateListener { firebase ->
        val uid = firebase.currentUser?.uid.orEmpty()
        if (uid != _state.value.uid) {
            reg?.remove(); sources = emptyList()
            _state.value = DriverSchedulesState(uid = uid, loading = uid.isNotEmpty())
            if (uid.isEmpty()) DriverReminders.clear(getApplication())
            else { activateReminders(); refresh() }
            updateNotificationPermission()
        } else if (uid.isEmpty()) DriverReminders.clear(getApplication())
    }

    init { auth.addAuthStateListener(authListener) }

    fun updateNotificationPermission() {
        val app = getApplication<Application>()
        val enabled = app.getSystemService(android.app.NotificationManager::class.java).areNotificationsEnabled()
        _state.update { it.copy(notificationsEnabled = enabled, exactAlarmsEnabled = DriverReminders.exactAllowed(app)) }
    }

    fun activateReminders() {
        val uid = state.value.uid
        if (uid.isEmpty()) return
        try {
            DriverReminders.activate(getApplication(), uid)
            if (sources.isNotEmpty()) updatePhoneView()
            _state.update { it.copy(reminderMessage = "Monity na telefonie · odświeżanie w tle co około godzinę") }
        } catch (_: Exception) {
            _state.update { it.copy(reminderMessage = "Nie udało się zaplanować monitów. Ponów odświeżenie.") }
        }
        updateNotificationPermission()
    }

    fun refresh() {
        val uid = state.value.uid
        if (uid.isEmpty()) return
        reg?.remove()
        _state.update { it.copy(loading = true, message = "") }
        reg = db.collection("users").document(uid).collection("driver_schedules")
            .addSnapshotListener(MetadataChanges.INCLUDE) { snap, error ->
                if (state.value.uid != uid) return@addSnapshotListener
                if (error != null) {
                    _state.update { it.copy(loading = false, fromCache = true,
                        message = "Nie można pobrać nowych terminów. Zachowano dane i monity telefonu.") }
                    return@addSnapshotListener
                }
                if (snap == null) return@addSnapshotListener
                sources = snap.documents.filter { it.getBoolean("active") != false }
                    .map { DriverSchedule.fromMap(it.id, it.data.orEmpty()) }.sortedBy { it.name.lowercase() }
                _state.update { it.copy(loading = false, fromCache = snap.metadata.isFromCache,
                    message = if (snap.metadata.isFromCache) "Pamięć telefonu · oczekiwanie na połączenie" else "Terminy z Brudnopisu · Firebase aktualne") }
                updatePhoneView(schedule = !snap.metadata.isFromCache || sources.isNotEmpty())
            }
    }

    private fun updatePhoneView(schedule: Boolean = true) {
        val uid = state.value.uid
        val visible = mutableListOf<DriverSchedule>()
        val hidden = mutableListOf<DriverSchedule>()
        sources.forEach { d ->
            val pref = preferences.read(uid, d.id)
            (if (pref.hidden) hidden else visible).add(pref.applyTo(d))
        }
        _state.update { it.copy(drivers = visible, hiddenDrivers = hidden) }
        if (schedule) {
            try { DriverReminders.update(getApplication(), uid, sources) }
            catch (_: Exception) { _state.update { it.copy(reminderMessage = "Plan zapisany; nie zaplanowano monitów. Ponów odświeżenie.") } }
        }
    }

    fun preferences(driver: DriverSchedule) = preferences.read(state.value.uid, driver.id)
    fun beginEdit() { _state.update { it.copy(saveError = "", savedDriverId = null) } }

    fun save(driver: DriverSchedule, plan: Map<String, String?>, leads: Set<Int>) {
        if (state.value.saving || state.value.uid.isEmpty()) return
        val uid = state.value.uid
        _state.update { it.copy(saving = true, saveError = "", savedDriverId = null) }
        try {
            val source = sources.find { it.id == driver.id } ?: error("Profil nie jest już dostępny.")
            check(source.revision == driver.revision) { "Brudnopis zmienił się podczas edycji. Wróć do karty i sprawdź nowy termin." }
            val current = preferences.read(uid, driver.id)
            preferences.save(uid, driver.id, current.copy(overrides = changedPhonePlan(source, current, plan), leads = leads))
            updatePhoneView()
            _state.update { it.copy(saving = false, savedDriverId = driver.id) }
        } catch (e: Exception) {
            _state.update { it.copy(saving = false, saveError = e.message ?: "Nie zapisano zmian w telefonie.") }
        }
    }

    fun restoreSource(driver: DriverSchedule) {
        val uid = state.value.uid
        if (uid.isEmpty()) return
        try {
            preferences.save(uid, driver.id, preferences.read(uid, driver.id).copy(overrides = emptyMap()))
            updatePhoneView()
        } catch (_: Exception) { _state.update { it.copy(message = "Nie przywrócono planu. Ponów operację.") } }
    }

    fun setHidden(driver: DriverSchedule, hidden: Boolean): Boolean {
        val uid = state.value.uid
        if (uid.isEmpty() || sources.none { it.id == driver.id }) return false
        return try {
            val current = preferences.read(uid, driver.id)
            preferences.save(uid, driver.id, current.copy(hidden = hidden))
            updatePhoneView()
            true
        } catch (_: Exception) {
            _state.update { it.copy(message = "Nie zapisano widoczności profilu w telefonie. Ponów operację.") }
            false
        }
    }

    override fun onCleared() { reg?.remove(); auth.removeAuthStateListener(authListener); super.onCleared() }
}
