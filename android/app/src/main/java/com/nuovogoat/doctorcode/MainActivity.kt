package com.nuovogoat.doctorcode

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import com.nuovogoat.doctorcode.ui.DoctorCodeRoot
import com.nuovogoat.doctorcode.ui.DoctorCodeTheme

/**
 * Single-activity Compose host.
 *
 * All connection state lives in ``MainViewModel``; rotating the phone therefore
 * keeps the WebSocket and the current project selection intact.
 */
class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        setContent {
            DoctorCodeTheme {
                DoctorCodeRoot()
            }
        }
    }
}
