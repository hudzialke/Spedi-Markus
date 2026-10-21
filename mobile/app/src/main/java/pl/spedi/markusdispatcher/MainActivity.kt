package pl.spedi.markusdispatcher

import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.os.Build
import android.Manifest
import android.content.pm.PackageManager
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.content.ContextCompat
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.contentDescription
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import kotlinx.coroutines.flow.collectLatest
import kotlinx.coroutines.withTimeoutOrNull

// Approved A2: compact two-column fields, original palette, 48dp touch targets.
private val Ink = Color(0xFF1A2B24)
private val Paper = Color(0xFFF3F0E8)
private val Lime = Color(0xFFC4F23A)
private val Muted = Color(0xFF5C6B64)
private val Card = Color(0xFFFFFCF6)
private val Line = Color(0xFFD9D2C4)

class MainActivity : ComponentActivity() {
    private val vm: JobsViewModel by viewModels()
    private val scheduleVm: DriverSchedulesViewModel by viewModels()
    private val destination = mutableStateOf<Pair<String, String>?>(null)
    private val notificationPermission = registerForActivityResult(ActivityResultContracts.RequestPermission()) { scheduleVm.updateNotificationPermission(); scheduleVm.activateReminders() }
    override fun onResume() { super.onResume(); scheduleVm.activateReminders(); scheduleVm.updateNotificationPermission() }
    private fun askNotificationPermission() {
        DriverReminders.createChannel(this)
        if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED)
            notificationPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
        else scheduleVm.activateReminders()
    }
    private fun readDestination(intent: Intent?) {
        val uid = intent?.getStringExtra("uid") ?: return
        val driver = intent.getStringExtra("driver_id") ?: return
        destination.value = uid to driver
    }
    override fun onNewIntent(intent: Intent) { super.onNewIntent(intent); setIntent(intent); readDestination(intent) }
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        DriverReminders.createChannel(this)
        readDestination(intent)
        setContent {
            MaterialTheme(colorScheme = lightColorScheme(primary = Ink, onPrimary = Lime,
                background = Paper, onBackground = Ink, surface = Card, onSurface = Ink,
                surfaceVariant = Paper, onSurfaceVariant = Muted, outlineVariant = Line)) {
                val state by vm.state.collectAsState()
                LaunchedEffect(state.signedIn) { if (state.signedIn) askNotificationPermission() }
                key(state.uid) { JobsApp(state, vm, ::copy, ::openWhatsApp, ::openEmail,
                    scheduleVm, destination.value?.takeIf { it.first == state.uid }?.second,
                    { destination.value = null }, ::askNotificationPermission) }
            }
        }
    }
    private fun copy(label: String, value: String) {
        (getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager)
            .setPrimaryClip(ClipData.newPlainText(label, value))
        Toast.makeText(this, "Skopiowano", Toast.LENGTH_SHORT).show()
    }
    private fun openSafely(intent: Intent) {
        try { startActivity(intent) }
        catch (_: android.content.ActivityNotFoundException) {
            Toast.makeText(this, "Brak aplikacji do obsługi tej akcji", Toast.LENGTH_LONG).show()
        }
    }
    private fun message(job: JobDto) = job.clientMessage.ifBlank { JobPresentation.copyAll(job) }
    private fun openWhatsApp(job: JobDto) {
        val phone = job.contactPhone.filter(Char::isDigit)
        openSafely(Intent(Intent.ACTION_VIEW, Uri.parse("https://wa.me/$phone?text=${Uri.encode(message(job))}")))
    }
    private fun openEmail(job: JobDto) {
        openSafely(Intent(Intent.ACTION_SENDTO, Uri.parse("mailto:${Uri.encode(job.contactEmail)}?subject=${Uri.encode(job.refNumbers)}&body=${Uri.encode(message(job))}")))
    }
}

@Composable
private fun JobsApp(state: UiState, vm: JobsViewModel, copy: (String, String) -> Unit,
                    whatsApp: (JobDto) -> Unit, email: (JobDto) -> Unit,
                    scheduleVm: DriverSchedulesViewModel, openDriverId: String?, consumed: () -> Unit, askPermission: () -> Unit) {
    var selected by rememberSaveable { mutableStateOf<String?>(null) }
    var showAbout by rememberSaveable { mutableStateOf(false) }
    var archiveTab by rememberSaveable { mutableStateOf(false) }
    var remindersTab by rememberSaveable { mutableStateOf(true) }
    LaunchedEffect(openDriverId) { if (openDriverId != null) { selected = null; remindersTab = true } }
    var confirmHide by rememberSaveable { mutableStateOf<String?>(null) }
    var confirmRestoreAll by rememberSaveable { mutableStateOf(false) }
    val snackbar = remember { SnackbarHostState() }
    val listScroll = rememberLazyListState()
    val archiveScroll = rememberLazyListState()
    BackHandler(selected != null) { selected = null }
    LaunchedEffect(vm) {
        vm.notices.collectLatest { notice ->
            if (notice.undoId != null) selected = null
            val result = withTimeoutOrNull(5000) {
                snackbar.showSnackbar(notice.message, actionLabel = if (notice.undoId != null) "Cofnij" else null,
                    duration = SnackbarDuration.Indefinite)
            }
            if (result == SnackbarResult.ActionPerformed) notice.undoId?.let(vm::restore)
        }
    }
    Scaffold(containerColor = Paper, snackbarHost = { SnackbarHost(snackbar) }) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 16.dp)) {
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                if (selected != null) TextButton(onClick = { selected = null }) { Text("← Wróć") }
                Text(if (selected == null) "Spedi Android App" else "Karta zlecenia",
                    modifier = Modifier.weight(1f), fontSize = 21.sp, fontWeight = FontWeight.Bold)
                TextButton(onClick = { showAbout = true }) { Text("Info") }
            }
            if (!state.signedIn) {
                Text("Zaloguj się, aby wyświetlić zlecenia.", color = Muted)
                LoginPane(state, vm)
            } else {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Text(state.message, Modifier.weight(1f), fontSize = 12.sp, color = Muted)
                    TextButton(onClick = vm::syncNow) { Text("Odśwież") }
                }
                if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
                if (selected == null) Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    FilterChip(selected = remindersTab, onClick = { remindersTab = true; archiveTab = false }, label = { Text("Monity") })
                    FilterChip(selected = !remindersTab && !archiveTab, onClick = { remindersTab = false; archiveTab = false }, label = { Text("Zlecenia") })
                    FilterChip(selected = !remindersTab && archiveTab, onClick = { remindersTab = false; archiveTab = true }, label = { Text("Archiwum") })
                }
                if (remindersTab && selected == null) {
                    TextButton(onClick = vm::signOut) { Text("Wyloguj") }
                    DriverCalendarPane(scheduleVm, openDriverId, consumed, askPermission)
                } else if (!state.archiveReady) {
                    Text("Odczytywanie archiwum telefonu…", Modifier.padding(vertical = 24.dp))
                    TextButton(onClick = vm::signOut) { Text("Wyloguj") }
                } else if (selected != null) {
                    val job = state.jobs.find { it.id == selected }
                    if (job == null) {
                        Text(if (state.serverConfirmed) "To zlecenie zostało usunięte z Firestore lub nie jest już udostępniane."
                            else "Zlecenie nie jest dostępne w pamięci telefonu. Połącz się z internetem i odśwież.", Modifier.padding(vertical = 24.dp))
                        if (selected in state.hidden) OutlinedButton(onClick = { vm.restore(selected!!); selected = null }) { Text("Usuń z archiwum telefonu") }
                    } else key(job.id) {
                        JobDetails(job, job.id in state.hidden, copy, whatsApp, email,
                            onHide = { confirmHide = job.id }, onRestore = { vm.restore(job.id); selected = null })
                    }
                } else {
                    Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                        if (!archiveTab) {
                            Checkbox(checked = state.activeOnly, onCheckedChange = vm::setActiveOnly)
                            Text("Tylko aktywne", fontSize = 13.sp)
                        } else if (state.hidden.isNotEmpty()) {
                            TextButton(onClick = { confirmRestoreAll = true }) { Text("Przywróć wszystkie") }
                        }
                        Spacer(Modifier.weight(1f))
                        TextButton(onClick = vm::signOut) { Text("Wyloguj") }
                    }
                    val jobs = ArchiveRules.visible(state.jobs, state.hidden, state.activeOnly)
                    if (archiveTab) {
                        if (state.hidden.isEmpty()) Text("Archiwum jest puste. Tutaj znajdziesz zlecenia ukryte na tym telefonie.", Modifier.padding(vertical = 24.dp))
                        LazyColumn(state = archiveScroll, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            items(state.hidden.values.toList(), key = { it.jobId }) { record ->
                                val job = state.jobs.find { it.id == record.jobId }
                                Column {
                                    JobRow(job ?: JobDto(id = record.jobId, refNumbers = record.reference, status = record.status),
                                        onClick = { selected = record.jobId })
                                    Text("Ukryte ręcznie na tym telefonie", fontSize = 12.sp, color = Muted)
                                    TextButton(onClick = { vm.restore(record.jobId) }) { Text("Przywróć") }
                                }
                            }
                        }
                    } else {
                        if (jobs.isEmpty() && !state.loading) Text("Brak zleceń w tym widoku. Sprawdź filtr lub Archiwum telefonu.", Modifier.padding(vertical = 24.dp))
                        LazyColumn(state = listScroll, verticalArrangement = Arrangement.spacedBy(10.dp)) {
                            items(jobs, key = { it.id }) { job -> JobRow(job) { selected = job.id } }
                        }
                    }
                }
            }
        }
    }
    if (showAbout) AlertDialog(onDismissRequest = { showAbout = false },
        title = { Text(stringResource(R.string.about_title)) },
        text = { LazyColumn { item { Text(stringResource(R.string.about_body)) } } },
        confirmButton = { TextButton(onClick = { showAbout = false }) { Text("OK") } })
    if (confirmHide != null) AlertDialog(onDismissRequest = { confirmHide = null },
        title = { Text("Ukryć zlecenie?") },
        text = { Text("Trafi do Archiwum telefonu. Dane w Dispatcherze pozostaną bez zmian.") },
        confirmButton = { TextButton(onClick = { confirmHide?.let(vm::hide); confirmHide = null }) { Text("Ukryj") } },
        dismissButton = { TextButton(onClick = { confirmHide = null }) { Text("Anuluj") } })
    if (confirmRestoreAll) AlertDialog(onDismissRequest = { confirmRestoreAll = false },
        title = { Text("Przywrócić wszystkie pozycje?") }, text = { Text("Archiwum telefonu zostanie opróżnione. Filtr „Tylko aktywne” nadal będzie działał.") },
        confirmButton = { TextButton(onClick = { vm.restoreAll(); confirmRestoreAll = false }) { Text("Przywróć") } },
        dismissButton = { TextButton(onClick = { confirmRestoreAll = false }) { Text("Anuluj") } })
}

@Composable
private fun LoginPane(state: UiState, vm: JobsViewModel) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        OutlinedTextField(state.email, vm::setEmail, label = { Text("E-mail Firebase") },
            modifier = Modifier.fillMaxWidth(), singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Email))
        OutlinedTextField(state.password, vm::setPassword, label = { Text("Hasło") },
            modifier = Modifier.fillMaxWidth(), singleLine = true, visualTransformation = PasswordVisualTransformation())
        Button(onClick = vm::signIn, enabled = !state.loading, modifier = Modifier.fillMaxWidth()) {
            Text(if (state.loading) "Logowanie…" else "Zaloguj")
        }
        if (state.message.isNotBlank()) Text(state.message, color = MaterialTheme.colorScheme.error)
    }
}

@Composable
private fun JobRow(job: JobDto, onClick: () -> Unit) {
    OutlinedCard(onClick = onClick, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.padding(14.dp), verticalArrangement = Arrangement.spacedBy(5.dp)) {
            Text(job.refNumbers.ifBlank { job.id }, fontWeight = FontWeight.Bold)
            Text(job.statusLabelPl, color = Muted, fontSize = 12.sp)
            Text(job.routeLabel, fontSize = 14.sp)
            if (job.driverName.isNotBlank()) Text(job.driverName, fontSize = 12.sp, color = Muted)
        }
    }
}

@Composable
private fun JobDetails(job: JobDto, hidden: Boolean, copy: (String, String) -> Unit,
                       whatsApp: (JobDto) -> Unit, email: (JobDto) -> Unit, onHide: () -> Unit, onRestore: () -> Unit) {
    LazyColumn(verticalArrangement = Arrangement.spacedBy(0.dp), contentPadding = PaddingValues(bottom = 24.dp)) {
        item {
            Button(onClick = { copy("Karta zlecenia", JobPresentation.copyAll(job)) }, modifier = Modifier.fillMaxWidth()) { Text("Kopiuj całą kartę") }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                OutlinedButton(onClick = { whatsApp(job) }, modifier = Modifier.weight(1f)) { Text("WhatsApp") }
                OutlinedButton(onClick = { email(job) }, modifier = Modifier.weight(1f)) { Text("E-mail") }
            }
        }
        JobPresentation.sections(job).forEach { section ->
            item(key = section.title) { Text(section.title, fontWeight = FontWeight.Bold, modifier = Modifier.padding(top = 20.dp, bottom = 8.dp)) }
            items(section.fields, key = { section.title + it.first }) { (label, value) ->
                Row(Modifier.fillMaxWidth().heightIn(min = 48.dp)
                    .semantics { contentDescription = "Kopiuj: $label, $value" }
                    .clickable(onClickLabel = "Kopiuj pełną wartość") { copy(label, value) }
                    .padding(vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
                    Text(label, Modifier.weight(0.30f).padding(end = 10.dp), fontSize = 12.sp, color = Muted)
                    Text(value, Modifier.weight(0.70f), fontSize = 14.sp)
                    Text("⧉", Modifier.padding(start = 8.dp), color = Muted)
                }
                HorizontalDivider(color = Line)
            }
        }
        item { OutlinedButton(onClick = if (hidden) onRestore else onHide,
            modifier = Modifier.fillMaxWidth().padding(top = 24.dp)) { Text(if (hidden) "Przywróć na listę" else "Ukryj na tym telefonie") } }
    }
}
