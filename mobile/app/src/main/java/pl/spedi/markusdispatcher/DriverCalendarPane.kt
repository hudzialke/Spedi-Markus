package pl.spedi.markusdispatcher

import android.app.NotificationManager
import android.content.Context
import android.content.Intent
import android.provider.Settings
import android.os.Build
import android.Manifest
import android.content.pm.PackageManager
import androidx.core.content.ContextCompat
import androidx.activity.compose.BackHandler
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.time.Instant
import java.time.LocalDate
import kotlinx.coroutines.delay

@Composable
fun ColumnScope.DriverCalendarPane(vm: DriverSchedulesViewModel, openDriverId: String?, consumed: () -> Unit, askPermission: () -> Unit) {
    val state by vm.state.collectAsState()
    var view by rememberSaveable(state.uid) { mutableStateOf("calendar") }
    var selected by rememberSaveable(state.uid) { mutableStateOf<String?>(null) }
    var editDriver by remember { mutableStateOf<DriverSchedule?>(null) }
    var now by remember { mutableStateOf(Instant.now()) }
    val context = LocalContext.current
    LaunchedEffect(state.uid) { selected = null; editDriver = null }
    LaunchedEffect(openDriverId) { if (openDriverId != null) { selected = openDriverId; consumed() } }
    LaunchedEffect(Unit) { while (true) { now = Instant.now(); delay(60_000) } }
    LaunchedEffect(state.savedDriverId) { if (state.savedDriverId != null) editDriver = null }
    BackHandler(selected != null) { if (editDriver != null) editDriver = null else selected = null }
    if (editDriver != null) {
        DriverPlanEditor(editDriver!!, vm.preferences(editDriver!!), state, onBack = { editDriver = null }, onSave = vm::save)
        return
    }
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
        Text(state.message, Modifier.weight(1f), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = { vm.refresh(); vm.activateReminders() }) { Text("Odśwież") }
    }
    if (state.loading) LinearProgressIndicator(Modifier.fillMaxWidth())
    val driver = state.drivers.find { it.id == selected }
    if (selected != null) {
        TextButton(onClick = { selected = null }) { Text("← Terminarz") }
        if (driver == null) {
            Text(if (state.loading) "Pobieranie profilu…"
                else if (state.hiddenDrivers.any { it.id == selected }) "Profil jest ukryty na tym telefonie. Przywrócisz go w „Ukryci”."
                else "Kierowca nie jest dostępny w tym koncie lub profilu nie ma już w Brudnopisie.")
        } else DriverDetails(driver, now, vm.preferences(driver), restore = { vm.restoreSource(driver) }, hide = {
            if (vm.setHidden(driver, true)) { selected = null; view = "visible" }
        }) { vm.beginEdit(); editDriver = driver }
        return
    }
    Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        FilterChip(view == "calendar", onClick = { view = "calendar" }, label = { Text("Terminarz") })
        FilterChip(view == "visible", onClick = { view = "visible" }, label = { Text("Kierowcy (${state.drivers.size})") })
        FilterChip(view == "hidden", onClick = { view = "hidden" }, label = { Text("Ukryci (${state.hiddenDrivers.size})") })
    }
    Text("W karcie kierowcy wybierz przypomnienia: 72 h · 48 h · 24 h", fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
    val notificationsEnabled = state.notificationsEnabled
    Row(Modifier.fillMaxWidth()) {
        Text(if (notificationsEnabled) state.reminderMessage else "Powiadomienia wyłączone w Androidzie",
            Modifier.weight(1f), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        TextButton(onClick = {
            if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(context, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) askPermission()
            else context.startActivity(Intent(Settings.ACTION_APP_NOTIFICATION_SETTINGS).putExtra(Settings.EXTRA_APP_PACKAGE, context.packageName))
        }) { Text("Powiadomienia") }
    }
    if (!state.exactAlarmsEnabled && Build.VERSION.SDK_INT >= 31) Row(Modifier.fillMaxWidth()) {
        Text("Zezwól na dokładne alarmy, aby monity pojawiały się o wybranej godzinie. Bez zgody Android może je opóźnić.",
            Modifier.weight(1f), fontSize = 12.sp)
        TextButton(onClick = { context.startActivity(Intent(Settings.ACTION_REQUEST_SCHEDULE_EXACT_ALARM,
            android.net.Uri.parse("package:${context.packageName}"))) }) { Text("Alarmy") }
    }
    if (view == "hidden") {
        Text("Ukrywanie dotyczy tego konta na tym telefonie. Ukryci kierowcy nie mają monitów.", fontSize = 12.sp)
        if (state.hiddenDrivers.isEmpty() && !state.loading) Text("Nie ma ukrytych profili.", Modifier.padding(vertical = 20.dp))
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(bottom = 24.dp)) {
            items(state.hiddenDrivers, key = { it.id }) { item ->
                ListItem(headlineContent = { Text(item.name, fontWeight = FontWeight.SemiBold) },
                    supportingContent = { Text("Profil ukryty · monity wyłączone") },
                    trailingContent = { TextButton(onClick = { vm.setHidden(item, false) }) { Text("Przywróć") } })
                HorizontalDivider()
            }
        }
    } else if (state.drivers.isEmpty() && !state.loading && state.hiddenDrivers.isNotEmpty()) {
        Text("Wszystkie profile są ukryte. Przywrócisz wybrane w „Ukryci”.", Modifier.padding(vertical = 20.dp))
    } else if (state.drivers.isEmpty() && !state.loading) {
        Text("Brak profili. W Spedi uruchom „Sync monity”, aby udostępnić pełną listę z Brudnopisu.", Modifier.padding(vertical = 20.dp))
    } else if (view == "visible") {
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(bottom = 24.dp)) {
            items(state.drivers, key = { it.id }) { item ->
                ListItem(headlineContent = { Text(item.name, fontWeight = FontWeight.SemiBold) },
                    supportingContent = { Column {
                        Text(item.tripLabel(now))
                        Text(if (item.week1 == null) "Start tygodnia 1 nieoznaczony" else "Tydzień 4: ${formatDriverDate(item.week4)}")
                        if (item.localEdited) Text("Plan zmieniony tylko na tym telefonie", fontSize = 12.sp)
                    } }, trailingContent = { TextButton(onClick = { vm.setHidden(item, true) }) { Text("Ukryj") } },
                    modifier = Modifier.clickable(onClickLabel = "Otwórz plan kierowcy") { selected = item.id })
                HorizontalDivider()
            }
        }
    } else {
        val events = state.drivers.flatMap { it.events() }.sortedBy { it.at }
        if (events.isEmpty() && !state.loading) Text("Nie ustalono terminów. Otwórz „Kierowcy” i uzupełnij plan kierowcy.", Modifier.padding(vertical = 20.dp))
        LazyColumn(Modifier.weight(1f), contentPadding = PaddingValues(bottom = 24.dp)) {
            events.groupBy { it.at.atZone(DriverZone).toLocalDate() }.forEach { (day, rows) ->
                item(key = "day-$day") {
                    Text(dayLabel(day, now), Modifier.padding(top = 20.dp, bottom = 6.dp), fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
                items(rows, key = { it.key }) { event ->
                    Column(Modifier.fillMaxWidth().clickable(onClickLabel = "Otwórz kierowcę") { selected = event.driverId }
                        .padding(vertical = 14.dp)) {
                        Text(event.title, fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
                        Text(event.driverName, fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
                        Text(formatDriverInstant(event.at), fontSize = 21.sp)
                        if (event.at.isBefore(now)) Text("Termin minął · sprawdź aktualność planu", fontSize = 12.sp, color = MaterialTheme.colorScheme.error)
                        val profile = state.drivers.first { it.id == event.driverId }
                        Text(profile.tripLabel(now), fontSize = 12.sp)
                        if (profile.localEdited) Text("Plan telefonu · zmiany nie trafiają do Spedi", fontSize = 12.sp)
                    }
                    HorizontalDivider()
                }
            }
        }
    }
}

private fun dayLabel(day: LocalDate, now: Instant): String {
    val today = now.atZone(DriverZone).toLocalDate()
    return (when (day) { today -> "Dziś · "; today.plusDays(1) -> "Jutro · "; else -> "" }) + formatDriverDate(day)
}

@Composable
private fun DriverDetails(driver: DriverSchedule, now: Instant, preferences: DriverPreferences, restore: () -> Unit, hide: () -> Unit, edit: () -> Unit) {
    LazyColumn(contentPadding = PaddingValues(bottom = 24.dp)) {
        item { Text(driver.name, fontSize = 21.sp, fontWeight = FontWeight.Bold); Text(driver.tripLabel(now)) }
        item { Text("Zmodyfikowany · Europe/Warsaw", fontSize = 12.sp, modifier = Modifier.padding(vertical = 10.dp)) }
        if (driver.localEdited) item { Text("Lokalna korekta. Zmienione pola pozostają w telefonie po kolejnych aktualizacjach z PC.", fontSize = 13.sp) }
        item { Text("Przypomnienia: " + preferences.leads.sortedDescending().joinToString(" · ") { "$it h" }.ifEmpty { "wyłączone dla kierowcy" }, fontSize = 13.sp) }
        item { DriverField("Tydzień 1", formatDriverDate(driver.week1)) }
        item { DriverField("Tydzień 4", driver.week4?.let { "${formatDriverDate(it)} – ${formatDriverDate(it.plusDays(6))}" } ?: "Nie ustalono") }
        item { DriverField("Start wyjazdu", formatDriverInstant(driver.departure)) }
        if (driver.departure == null && driver.anchor != null) item { DriverField("Kotwica w Brudnopisie", formatDriverInstant(driver.anchor)) }
        item { DriverField("Powrót planowany", formatDriverInstant(driver.plannedReturn)) }
        item { DriverField("OT z rekompensatami · najpóźniej", formatDriverInstant(driver.restStartBy)) }
        if ("rest_start_by" in preferences.overrides) item { DriverField("Termin OT z Brudnopisu", formatDriverInstant(driver.sourceRestStartBy)) }
        item { DriverField("Kolejny OT · 6×24 h", formatDriverInstant(driver.nextWeeklyRest)) }
        items(driver.compensations) { debt ->
            Column(Modifier.padding(vertical = 12.dp)) {
                Text("Rekompensata ${debt.minutes / 60} h ${debt.minutes % 60} min", fontWeight = FontWeight.SemiBold)
                Text("Termin: ${formatDriverInstant(debt.due)}", fontSize = 14.sp)
                if (debt.latestStart != null) Text("Rozpoczęcie najpóźniej: ${formatDriverInstant(debt.latestStart)}", fontSize = 14.sp)
                if (debt.mobilityPair) Text("Rekompensaty bezpośrednio przed regularnym 45 h", fontSize = 12.sp)
            }
        }
        item { Text("Wyliczenie obejmuje zaplanowane bloki z widoku zmodyfikowanego. Zmiana powrotu nie przesuwa terminu odpoczynku.", fontSize = 12.sp, modifier = Modifier.padding(vertical = 12.dp)) }
        item { DriverField("Ostatnia aktywność w profilu", formatDriverInstant(driver.lastActivity)); DriverField("Sync PC → Firebase", formatDriverInstant(driver.syncedAt)) }
        item { Button(onClick = edit, modifier = Modifier.fillMaxWidth()) { Text("Edytuj plan") } }
        if (driver.localEdited) item { OutlinedButton(onClick = restore, modifier = Modifier.fillMaxWidth()) { Text("Przywróć daty z Brudnopisu") } }
        item {
            OutlinedButton(onClick = hide, modifier = Modifier.fillMaxWidth()) { Text("Ukryj profil w telefonie") }
            Text("Profil zniknie z listy i terminarza, a jego monity zostaną wyłączone. Przywrócisz go w „Ukryci”.", fontSize = 12.sp)
        }
        items(driver.findings) { finding -> Text(finding, fontSize = 13.sp, modifier = Modifier.padding(vertical = 8.dp)) }
    }
}

@Composable
private fun DriverField(label: String, value: String) {
    Row(Modifier.fillMaxWidth().heightIn(min = 48.dp).padding(vertical = 10.dp)) {
        Text(label, Modifier.weight(.35f).padding(end = 10.dp), fontSize = 12.sp, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, Modifier.weight(.65f), fontSize = 14.sp)
    }
    HorizontalDivider()
}

@Composable
private fun DriverPlanEditor(driver: DriverSchedule, preferences: DriverPreferences, state: DriverSchedulesState, onBack: () -> Unit,
                             onSave: (DriverSchedule, Map<String, String?>, Set<Int>) -> Unit) {
    var week1 by rememberSaveable(driver.id) { mutableStateOf(driver.week1?.let(::formatDriverDate).orEmpty()) }
    var departure by rememberSaveable(driver.id) { mutableStateOf(driver.departure?.let(::formatDriverInstant).orEmpty()) }
    var plannedReturn by rememberSaveable(driver.id) { mutableStateOf(driver.plannedReturn?.let(::formatDriverInstant).orEmpty()) }
    var departureOccurrence by rememberSaveable { mutableStateOf<Int?>(null) }
    var returnOccurrence by rememberSaveable { mutableStateOf<Int?>(null) }
    var rest by rememberSaveable(driver.id) { mutableStateOf(driver.restStartBy?.let(::formatDriverInstant).orEmpty()) }
    var restOccurrence by rememberSaveable { mutableStateOf<Int?>(null) }
    var leads by rememberSaveable(driver.id) { mutableStateOf(preferences.leads.toList()) }
    var error by rememberSaveable { mutableStateOf("") }
    LazyColumn(contentPadding = PaddingValues(bottom = 24.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        item { TextButton(onClick = onBack, enabled = !state.saving) { Text("← Karta kierowcy") }; Text("Edytuj plan · ${driver.name}", fontSize = 20.sp) }
        item { Text("Daty i godziny w Polsce. Puste pole usuwa termin. Start tygodnia 1 musi być poniedziałkiem.", fontSize = 13.sp) }
        item { Text("Zmiany zapisują się tylko na tym telefonie, również bez internetu. Spedi nie pobiera tych zmian.", fontSize = 13.sp) }
        item { Text("Przypomnij przed terminem:", fontSize = 13.sp)
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) { ReminderLeadHours.forEach { h ->
                FilterChip(h in leads, { leads = if (h in leads) leads - h else leads + h }, enabled = !state.saving, label = { Text("$h h") })
            } }
        }
        item { OutlinedTextField(week1, { week1 = it }, label = { Text("Start tygodnia 1") }, placeholder = { Text("dd.MM.rrrr") }, enabled = !state.saving, singleLine = true, modifier = Modifier.fillMaxWidth()) }
        item { OutlinedTextField(departure, { departure = it }, label = { Text("Start wyjazdu") }, placeholder = { Text("dd.MM.rrrr HH:mm") }, enabled = !state.saving, singleLine = true, modifier = Modifier.fillMaxWidth()) }
        item { OutlinedTextField(plannedReturn, { plannedReturn = it }, label = { Text("Powrót planowany") }, placeholder = { Text("dd.MM.rrrr HH:mm") }, enabled = !state.saving, singleLine = true, modifier = Modifier.fillMaxWidth()) }
        item { OutlinedTextField(rest, { rest = it }, label = { Text("OT z rekompensatami · najpóźniej") }, placeholder = { Text("dd.MM.rrrr HH:mm") }, enabled = !state.saving, singleLine = true, modifier = Modifier.fillMaxWidth()) }
        item { Text("Termin z Brudnopisu: ${formatDriverInstant(driver.sourceRestStartBy)}. Ręczna data zmienia monit w telefonie; nie przelicza rekompensat.", fontSize = 12.sp) }
        item { Text("Gdy godzina wyjazdu występuje dwukrotnie:", fontSize = 12.sp)
            Row { FilterChip(departureOccurrence == 0, { departureOccurrence = 0 }, label = { Text("Pierwsza · letni") }); Spacer(Modifier.width(8.dp)); FilterChip(departureOccurrence == 1, { departureOccurrence = 1 }, label = { Text("Druga · zimowy") }) }
        }
        item { Text("Gdy godzina powrotu występuje dwukrotnie:", fontSize = 12.sp)
            Row { FilterChip(returnOccurrence == 0, { returnOccurrence = 0 }, label = { Text("Pierwsza · letni") }); Spacer(Modifier.width(8.dp)); FilterChip(returnOccurrence == 1, { returnOccurrence = 1 }, label = { Text("Druga · zimowy") }) }
        }
        item { Text("Gdy godzina odpoczynku występuje dwukrotnie:", fontSize = 12.sp)
            Row { FilterChip(restOccurrence == 0, { restOccurrence = 0 }, label = { Text("Pierwsza · letni") }); Spacer(Modifier.width(8.dp)); FilterChip(restOccurrence == 1, { restOccurrence = 1 }, label = { Text("Druga · zimowy") }) }
        }
        if (error.isNotEmpty()) item { Text(error, color = MaterialTheme.colorScheme.error) }
        if (state.saveError.isNotEmpty()) item { Text(state.saveError, color = MaterialTheme.colorScheme.error) }
        item { Button(enabled = !state.saving, modifier = Modifier.fillMaxWidth(), onClick = {
            try { val plan = buildDriverPlan(week1, departure, plannedReturn, departureOccurrence, returnOccurrence,
                driver.departure,
                driver.plannedReturn);
                val restAt = if (driver.restStartBy != null && rest == formatDriverInstant(driver.restStartBy)) driver.restStartBy.toString() else parsePlanTime(rest, restOccurrence)
                error = ""; onSave(driver, plan + ("rest_start_by" to restAt), leads.toSet()) }
            catch (e: Exception) { error = e.message?.takeIf { e is IllegalArgumentException } ?: "Sprawdź format dat: dd.MM.rrrr HH:mm." }
        }) { Text(if (state.saving) "Zapisywanie…" else "Zapisz w telefonie") } }
    }
}
