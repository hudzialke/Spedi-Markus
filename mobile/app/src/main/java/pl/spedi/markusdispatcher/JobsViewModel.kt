package pl.spedi.markusdispatcher

import android.app.Application
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import com.google.firebase.auth.FirebaseAuth
import com.google.firebase.firestore.FirebaseFirestore
import com.google.firebase.firestore.ListenerRegistration
import com.google.firebase.firestore.MetadataChanges
import kotlinx.coroutines.Job
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.channels.Channel
import kotlinx.coroutines.flow.*
import kotlinx.coroutines.launch
import kotlinx.coroutines.tasks.await
import kotlinx.coroutines.withTimeoutOrNull
import java.util.Locale

data class UiState(
    val email: String = "", val password: String = "", val signedIn: Boolean = false,
    val loading: Boolean = false, val message: String = "", val jobs: List<JobDto> = emptyList(),
    val activeOnly: Boolean = true, val hidden: Map<String, HiddenJob> = emptyMap(),
    val archiveReady: Boolean = false, val serverConfirmed: Boolean = false, val uid: String = "",
)
data class UiNotice(val message: String, val undoId: String? = null)

class JobsViewModel(application: Application) : AndroidViewModel(application) {
    private val auth = FirebaseAuth.getInstance()
    private val db = FirebaseFirestore.getInstance()
    private val archive = PhoneArchive(application)
    private var jobsReg: ListenerRegistration? = null
    private var driversReg: ListenerRegistration? = null
    private var archiveJob: Job? = null
    private var names: Map<String, String> = emptyMap()
    private val _state = MutableStateFlow(UiState())
    val state = _state.asStateFlow()
    private val events = Channel<UiNotice>(Channel.BUFFERED)
    val notices = events.receiveAsFlow()
    private val authListener = FirebaseAuth.AuthStateListener { firebase ->
        val uid = firebase.currentUser?.uid.orEmpty()
        if (uid != _state.value.uid) bindAccount(uid)
    }

    init { auth.addAuthStateListener(authListener) }
    fun setEmail(v: String) { _state.update { it.copy(email = v) } }
    fun setPassword(v: String) { _state.update { it.copy(password = v) } }
    fun setActiveOnly(v: Boolean) { _state.update { it.copy(activeOnly = v) } }
    fun signIn() { viewModelScope.launch {
        _state.update { it.copy(loading = true, message = "") }
        try {
            auth.signInWithEmailAndPassword(state.value.email.trim(), state.value.password).await()
            _state.update { it.copy(loading = false, password = "") }
        } catch (e: CancellationException) { throw e
        } catch (_: Exception) { _state.update { it.copy(loading = false, message = "Logowanie nieudane. Sprawdź dane i połączenie.") } }
    } }
    fun signOut() { viewModelScope.launch {
        try { DriverReminders.clear(getApplication()) }
        catch (e: CancellationException) { throw e }
        catch (_: Exception) { /* UID filter also blocks messages for a signed-out account. */ }
        finally {
            getApplication<Application>().getSystemService(android.app.NotificationManager::class.java).cancelAll()
            auth.signOut(); bindAccount("")
        }
    } }
    private fun bindAccount(uid: String) {
        jobsReg?.remove(); driversReg?.remove(); archiveJob?.cancel(); names = emptyMap()
        while (events.tryReceive().isSuccess) { /* discard previous account notices */ }
        _state.value = UiState(uid = uid, signedIn = uid.isNotEmpty(), loading = uid.isNotEmpty())
        if (uid.isEmpty()) return
        archiveJob = viewModelScope.launch {
            try { archive.observe(uid).collect { hidden ->
                if (state.value.uid == uid) {
                    _state.update { it.copy(hidden = hidden, archiveReady = true) }
                    reconcile(uid)
                }
            } } catch (e: CancellationException) { throw e
            } catch (_: Exception) { if (state.value.uid == uid) _state.update { it.copy(archiveReady = false, message = "Nie można odczytać archiwum telefonu. Ponów odświeżenie.") } }
        }
        driversReg = db.collection("users").document(uid).collection("drivers").addSnapshotListener { snap, _ ->
            if (state.value.uid != uid || snap == null) return@addSnapshotListener
            names = snap.documents.associate { it.id to (it.getString("display_name") ?: it.id) }
            _state.update { it.copy(jobs = it.jobs.map { j -> j.copy(driverName = names[j.driverId] ?: j.driverName) }) }
        }
        listenJobs(uid)
    }
    fun syncNow() {
        val uid = state.value.uid
        if (uid.isEmpty()) return
        if (!state.value.archiveReady) { bindAccount(uid); return }
        _state.update { it.copy(message = "Odświeżam…", serverConfirmed = false) }
        listenJobs(uid)
    }
    private fun listenJobs(uid: String) {
        jobsReg?.remove()
        jobsReg = db.collection("users").document(uid).collection("jobs")
            .addSnapshotListener(MetadataChanges.INCLUDE) { snap, err ->
                if (state.value.uid != uid) return@addSnapshotListener
                if (err != null) {
                    _state.update { it.copy(loading = false, serverConfirmed = false, message = "Nie można pobrać zleceń. Sprawdź połączenie i uprawnienia; spróbuj Odśwież.") }
                    return@addSnapshotListener
                }
                if (snap == null) return@addSnapshotListener
                val jobs = snap.documents.map { doc ->
                    val d = doc.data.orEmpty()
                    fun value(key: String) = d[key]?.toString().orEmpty()
                    val driverId = value("driver_id")
                    JobDto(id = doc.id, refNumbers = value("ref_numbers"), status = value("status"),
                        driverId = driverId, driverName = names[driverId] ?: value("driver_name").ifBlank { driverId },
                        clientName = value("client_name"), loadAddress = value("load_address"), loadDatetime = value("load_datetime"),
                        loadGps = formatGps(d["load_lat"], d["load_lon"]), unloadAddress = value("unload_address"),
                        unloadDatetime = value("unload_datetime"), unloadGps = formatGps(d["unload_lat"], d["unload_lon"]),
                        pallets = value("pallets"), notes = value("notes"), mapsUrl = value("maps_url"),
                        contactPhone = value("contact_phone"), contactEmail = value("contact_email"),
                        clientMessage = value("client_message"), updatedAt = value("updated_at"))
                }.sortedByDescending { it.updatedAt }
                val confirmed = !snap.metadata.isFromCache
                _state.update { it.copy(jobs = jobs, loading = false, serverConfirmed = confirmed,
                    message = if (confirmed) "Dane aktualne" else "Dane z pamięci telefonu · oczekiwanie na połączenie") }
                reconcile(uid)
            }
    }
    private fun reconcile(uid: String) {
        val current = state.value
        if (current.archiveReady && current.serverConfirmed && current.jobs.any { j -> current.hidden[j.id]?.let { ArchiveRules.shouldRestore(it, j.status) } == true }) {
            localAction(uid) { archive.reconcile(uid, state.value.jobs) }
        }
    }
    private fun localAction(uid: String = state.value.uid, action: suspend () -> Unit) { viewModelScope.launch {
        if (uid.isEmpty() || state.value.uid != uid) return@launch
        try { action() } catch (e: CancellationException) { throw e
        } catch (_: Exception) { if (state.value.uid == uid) events.send(UiNotice("Nie udało się zapisać archiwum telefonu. Spróbuj ponownie.")) }
    } }
    fun hide(id: String) {
        val current = state.value
        val job = current.jobs.find { it.id == id } ?: return
        if (!current.archiveReady) return
        localAction(current.uid) {
            archive.hide(current.uid, job)
            if (state.value.uid == current.uid) events.send(UiNotice("Ukryto na tym telefonie", id))
        }
    }
    fun restore(id: String) { val uid = state.value.uid; localAction(uid) { archive.restore(uid, setOf(id)) } }
    fun restoreAll() { val current = state.value; localAction(current.uid) { archive.restore(current.uid, current.hidden.keys) } }
    override fun onCleared() {
        jobsReg?.remove(); driversReg?.remove(); auth.removeAuthStateListener(authListener)
        super.onCleared()
    }
}

internal fun formatGps(lat: Any?, lon: Any?): String {
    val a = lat?.toString()?.toDoubleOrNull() ?: return ""
    val b = lon?.toString()?.toDoubleOrNull() ?: return ""
    if (!a.isFinite() || !b.isFinite()) return ""
    return String.format(Locale.US, "%.6f, %.6f", a, b)
}
