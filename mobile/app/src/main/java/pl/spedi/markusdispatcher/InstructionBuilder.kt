package pl.spedi.markusdispatcher

/** Tekst instrukcji dla kierowcy — odpowiednik desktopowego build_instruction. */
object InstructionBuilder {
    fun build(job: JobDto, footer: String = DEFAULT_FOOTER): String {
        val sections = mutableListOf<String>()

        val load = buildBlock(
            headed("ZAŁADUNEK", job.loadDatetime),
            listOf(
                "Adres" to job.loadAddress,
                "GPS" to job.loadGps,
            ),
        )
        if (job.loadAddress.isNotBlank() || job.loadDatetime.isNotBlank() || job.loadGps.isNotBlank()) {
            sections += load
        }

        val detailsRows = mutableListOf(
            "Ref" to job.refNumbers,
            "Palety" to job.pallets,
            "Uwagi" to job.notes,
        )
        if (job.clientName.isNotBlank()) {
            detailsRows.add(0, "Klient" to job.clientName)
        }
        val details = buildBlock("SZCZEGÓŁY", detailsRows)
        if (details.lines().size > 1) {
            sections += details
        }

        val unload = buildBlock(
            headed("ROZŁADUNEK", job.unloadDatetime),
            listOf(
                "Adres" to job.unloadAddress,
                "GPS" to job.unloadGps,
            ),
        )
        if (job.unloadAddress.isNotBlank() || job.unloadDatetime.isNotBlank() || job.unloadGps.isNotBlank()) {
            sections += unload
        }

        if (job.mapsUrl.isNotBlank()) {
            sections += "TRASA:\n${job.mapsUrl}"
        }

        val foot = footer.trim()
        if (foot.isNotEmpty()) {
            sections += foot
        }
        return sections.joinToString("\n\n")
    }

    private fun headed(base: String, whenText: String): String {
        val w = whenText.trim()
        return if (w.isEmpty()) base else "$base $w"
    }

    private fun buildBlock(title: String, rows: List<Pair<String, String>>): String {
        val lines = mutableListOf(title)
        for ((label, value) in rows) {
            val text = value.trim()
            if (text.isEmpty()) continue
            if ('\n' in text) {
                lines += "$label:"
                text.lineSequence().forEach { lines += "  $it" }
            } else {
                lines += "$label: $text"
            }
        }
        return lines.joinToString("\n")
    }

    const val DEFAULT_FOOTER =
        "Pamiętaj o foto towaru i numerze identyfikacyjnym CMR " +
            "w prawym górnym rogu dokumentu."
}
