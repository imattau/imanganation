package io.github.imattau.imanganation

import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.setValue
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import java.io.IOException

private val Paper = Color(0xFFF4E4CB)
private val Ink = Color(0xFF293A56)
private val Canvas = Color(0xFFFFFCF6)
private val MutedInk = Color(0xFF697386)

private val SampleScript = """PAGE 1
[SCENE: Rooftop — evening]
PANEL 1
[SHOT: wide shot]
[CHARACTERS: Mio]
[FRAME: wide, large]
[ACTION]
Mio watches the first stars appear above the city.

PANEL 2
[SHOT: close-up]
[CHARACTERS: Mio]
[ACTION]
She unfolds the letter she promised not to read.
"""

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContent { ImanganationTheme { ScriptEditor() } }
    }
}

@Composable
private fun ImanganationTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = lightColorScheme(
            primary = Ink,
            onPrimary = Paper,
            background = Paper,
            surface = Canvas,
            onSurface = Ink,
            secondary = MutedInk,
        ),
        content = content,
    )
}

@Composable
private fun ScriptEditor() {
    val context = LocalContext.current
    var draft by rememberSaveable { mutableStateOf(SampleScript) }
    var fileName by rememberSaveable { mutableStateOf("Untitled story") }
    var status by rememberSaveable { mutableStateOf("Example script loaded. Save a copy to keep your draft.") }

    fun readDocument(uri: Uri) {
        try {
            val text = context.contentResolver.openInputStream(uri)
                ?.bufferedReader(Charsets.UTF_8)
                ?.use { it.readText() }
                ?: throw IOException("The selected document could not be opened.")
            draft = text
            fileName = uri.lastPathSegment?.substringAfterLast('/') ?: "Story script"
            status = "Opened $fileName"
        } catch (error: Exception) {
            status = "Couldn't open the script: ${error.localizedMessage ?: "read failed"}"
        }
    }

    val openScript = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri != null) readDocument(uri)
    }
    val saveScript = rememberLauncherForActivityResult(
        ActivityResultContracts.CreateDocument("text/markdown"),
    ) { uri ->
        if (uri != null) writeDocument(uri, draft, context.contentResolver) { result ->
            status = result
            if (result.startsWith("Saved")) {
                fileName = uri.lastPathSegment?.substringAfterLast('/') ?: "Story script"
            }
        }
    }

    Surface(modifier = Modifier.fillMaxSize(), color = Paper) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 20.dp, vertical = 18.dp),
            verticalArrangement = Arrangement.spacedBy(14.dp),
        ) {
            Column {
                Text(
                    text = "IMANGANATION",
                    color = MutedInk,
                    fontSize = 12.sp,
                    fontWeight = FontWeight.Bold,
                    letterSpacing = 2.sp,
                )
                Spacer(Modifier.height(5.dp))
                Text("Story draft", style = MaterialTheme.typography.headlineMedium,
                    fontWeight = FontWeight.Bold)
            }

            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                OutlinedButton(onClick = { openScript.launch(arrayOf("text/*")) }) {
                    Text("Open script")
                }
                Button(onClick = { saveScript.launch("story.md") }) {
                    Text("Save a copy")
                }
            }

            Text(
                text = fileName,
                color = MutedInk,
                style = MaterialTheme.typography.labelLarge,
            )
            OutlinedTextField(
                value = draft,
                onValueChange = { draft = it },
                modifier = Modifier
                    .fillMaxWidth()
                    .weight(1f),
                label = { Text("Canonical manga script") },
                placeholder = { Text("PAGE 1\nPANEL 1\n[ACTION]\n...") },
                minLines = 12,
                maxLines = 24,
                textStyle = MaterialTheme.typography.bodyMedium.copy(
                    fontFamily = androidx.compose.ui.text.font.FontFamily.Monospace,
                    lineHeight = 22.sp,
                ),
            )
            Text(
                text = status,
                color = MutedInk,
                style = MaterialTheme.typography.bodySmall,
            )
            Text(
                text = "Write on your phone; continue drawing and rendering in GIMP on desktop.",
                color = Ink,
                style = MaterialTheme.typography.bodySmall,
                modifier = Modifier
                    .fillMaxWidth()
                    .background(Canvas, MaterialTheme.shapes.medium)
                    .padding(12.dp),
            )
        }
    }
}

private fun writeDocument(
    uri: Uri,
    text: String,
    resolver: android.content.ContentResolver,
    report: (String) -> Unit,
) {
    try {
        resolver.openOutputStream(uri, "wt")
            ?.bufferedWriter(Charsets.UTF_8)
            ?.use { it.write(text) }
            ?: throw IOException("The selected location could not be written.")
        report("Saved ${uri.lastPathSegment?.substringAfterLast('/') ?: "story script"}")
    } catch (error: Exception) {
        report("Couldn't save the script: ${error.localizedMessage ?: "write failed"}")
    }
}
