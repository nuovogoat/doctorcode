package com.nuovogoat.doctorcode.ui

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

private val DoctorDarkColors = darkColorScheme(
    primary = Color(0xFF7DD3FC),
    onPrimary = Color(0xFF00202E),
    primaryContainer = Color(0xFF0E4A63),
    onPrimaryContainer = Color(0xFFD7F3FF),
    secondary = Color(0xFFA7F3D0),
    onSecondary = Color(0xFF003824),
    background = Color(0xFF0B1220),
    onBackground = Color(0xFFE5E7EB),
    surface = Color(0xFF111A2E),
    onSurface = Color(0xFFE5E7EB),
    surfaceVariant = Color(0xFF1E293B),
    onSurfaceVariant = Color(0xFFCBD5E1),
    outline = Color(0xFF334155),
    error = Color(0xFFF87171),
    onError = Color(0xFF2A0A0A),
    errorContainer = Color(0xFF5B1A1A),
    onErrorContainer = Color(0xFFFFDAD6),
)

@Composable
fun DoctorCodeTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = DoctorDarkColors,
        typography = Typography(),
        content = content,
    )
}

fun statusColor(status: String): Color = when (status.uppercase()) {
    "SUCCESS" -> Color(0xFF34D399)
    "FAILURE" -> Color(0xFFF87171)
    else -> Color(0xFFFBBF24)
}
