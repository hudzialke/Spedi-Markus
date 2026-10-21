package pl.spedi.markusdispatcher

data class CardSection(val title: String, val fields: List<Pair<String, String>>)

object JobPresentation {
    fun sections(j: JobDto): List<CardSection> = listOf(
        CardSection("Załadunek", listOf("Adres" to j.loadAddress, "Termin" to j.loadDatetime, "GPS" to j.loadGps)),
        CardSection("Szczegóły", listOf("Referencja" to j.refNumbers, "Status" to j.statusLabelPl,
            "Kierowca" to j.driverName, "Klient" to j.clientName, "Palety" to j.pallets)),
        CardSection("Rozładunek", listOf("Adres" to j.unloadAddress, "Termin" to j.unloadDatetime, "GPS" to j.unloadGps)),
        CardSection("Kontakt", listOf("Telefon" to j.contactPhone, "E-mail" to j.contactEmail, "Wiadomość" to j.clientMessage)),
        CardSection("Trasa", listOf("Link Maps" to j.mapsUrl)),
        CardSection("Uwagi", listOf("Uwagi" to j.notes)),
        CardSection("Informacje o zleceniu", listOf("ID zlecenia" to j.id, "ID kierowcy" to j.driverId, "Aktualizacja" to j.updatedAt)),
    ).map { it.copy(fields = it.fields.filter { f -> f.second.isNotBlank() }) }.filter { it.fields.isNotEmpty() }

    fun copyAll(j: JobDto) = sections(j).joinToString("\n\n") { section ->
        section.title + "\n" + section.fields.joinToString("\n") { (label, value) -> "$label: $value" }
    }
}

data class HiddenJob(val jobId: String, val status: String, val reason: String = "manual", val reference: String = jobId)

object ArchiveRules {
    val terminal = setOf("done", "cancelled")
    val active = setOf("draft", "assigned", "loading", "en_route", "unloading")
    fun shouldRestore(hidden: HiddenJob, currentStatus: String): Boolean =
        hidden.status in terminal && currentStatus in active

    fun visible(jobs: List<JobDto>, hidden: Map<String, HiddenJob>, activeOnly: Boolean) =
        jobs.filter { it.id !in hidden && (!activeOnly || it.status !in terminal) }
}
