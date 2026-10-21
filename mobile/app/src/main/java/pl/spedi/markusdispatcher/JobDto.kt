package pl.spedi.markusdispatcher

data class JobDto(
    val id: String = "",
    val refNumbers: String = "",
    val status: String = "draft",
    val driverId: String = "",
    val driverName: String = "",
    val clientName: String = "",
    val loadAddress: String = "",
    val loadDatetime: String = "",
    val loadGps: String = "",
    val unloadAddress: String = "",
    val unloadDatetime: String = "",
    val unloadGps: String = "",
    val pallets: String = "",
    val notes: String = "",
    val mapsUrl: String = "",
    val contactPhone: String = "",
    val contactEmail: String = "",
    val clientMessage: String = "",
    val updatedAt: String = "",
) {
    val routeLabel: String
        get() {
            val a = loadAddress.ifBlank { "—" }.substringBefore(",").trim()
            val b = unloadAddress.ifBlank { "—" }.substringBefore(",").trim()
            return "$a → $b"
        }

    val statusLabelPl: String
        get() = when (status) {
            "draft" -> "szkic"
            "assigned" -> "przypisane"
            "loading" -> "załadunek"
            "en_route" -> "w drodze"
            "unloading" -> "rozładunek"
            "done" -> "zakończone"
            "cancelled" -> "anulowane"
            else -> status
        }
}
