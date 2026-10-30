package com.nuovogoat.doctorcode.ui

import android.content.Context
import android.content.Intent
import android.os.Bundle
import android.speech.RecognitionListener
import android.speech.RecognizerIntent
import android.speech.SpeechRecognizer
import com.nuovogoat.doctorcode.R
import java.util.Locale

/**
 * Small wrapper around the platform speech recognizer.
 *
 * Recognition is optional: a device without a speech service reports a normal
 * error instead of blocking the text prompt.  Callbacks run on the main thread.
 */
class VoiceController(private val context: Context) {
    var onResult: (String) -> Unit = {}
    var onError: (String) -> Unit = {}
    var onListening: (Boolean) -> Unit = {}

    private var recognizer: SpeechRecognizer? = null
    private var listening = false

    fun start() {
        if (!SpeechRecognizer.isRecognitionAvailable(context)) {
            onError(context.getString(R.string.voice_unavailable))
            return
        }
        val engine = recognizer ?: SpeechRecognizer.createSpeechRecognizer(context).also {
            it.setRecognitionListener(listener)
            recognizer = it
        }
        val intent = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH).apply {
            putExtra(
                RecognizerIntent.EXTRA_LANGUAGE_MODEL,
                RecognizerIntent.LANGUAGE_MODEL_FREE_FORM,
            )
            putExtra(RecognizerIntent.EXTRA_PARTIAL_RESULTS, false)
            putExtra(RecognizerIntent.EXTRA_MAX_RESULTS, 3)
            putExtra(RecognizerIntent.EXTRA_LANGUAGE, Locale.getDefault().toLanguageTag())
            putExtra(RecognizerIntent.EXTRA_PROMPT, context.getString(R.string.voice_prompt))
        }
        listening = true
        onListening(true)
        try {
            engine.startListening(intent)
        } catch (exc: Exception) {
            listening = false
            onListening(false)
            onError(exc.message ?: context.getString(R.string.voice_unavailable))
        }
    }

    fun stop() {
        if (!listening) return
        runCatching { recognizer?.stopListening() }
        listening = false
        onListening(false)
    }

    fun destroy() {
        runCatching { recognizer?.destroy() }
        recognizer = null
        listening = false
    }

    private val listener = object : RecognitionListener {
        override fun onReadyForSpeech(params: Bundle?) = Unit
        override fun onBeginningOfSpeech() = Unit
        override fun onRmsChanged(rmsdB: Float) = Unit
        override fun onBufferReceived(buffer: ByteArray?) = Unit

        override fun onEndOfSpeech() {
            listening = false
            onListening(false)
        }

        override fun onError(error: Int) {
            listening = false
            onListening(false)
            onError(context.getString(R.string.voice_error, error))
        }

        override fun onResults(results: Bundle?) {
            listening = false
            onListening(false)
            val text = results
                ?.getStringArrayList(SpeechRecognizer.RESULTS_RECOGNITION)
                ?.firstOrNull()
                .orEmpty()
            if (text.isBlank()) {
                onError(context.getString(R.string.voice_empty))
            } else {
                onResult(text)
            }
        }

        override fun onPartialResults(partialResults: Bundle?) = Unit
        override fun onEvent(eventType: Int, params: Bundle?) = Unit
    }
}
