package com.example.oussamacutter.ui.screens

import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.oussamacutter.data.GeminiService
import com.example.oussamacutter.data.ProjectRepository
import com.example.oussamacutter.model.AiModelOption
import com.example.oussamacutter.model.FramingMode
import com.example.oussamacutter.model.SafetyActionMode
import com.example.oussamacutter.ui.theme.*
import kotlinx.coroutines.launch

@Composable
fun CreateScreen(
    onProcessingStarted: () -> Unit,
    modifier: Modifier = Modifier
) {
    var videoSourceUrl by remember { mutableStateOf("https://www.youtube.com/watch?v=demo_podcast_arabic") }
    var selectedVideoUri by remember { mutableStateOf<Uri?>(null) }
    var selectedModel by remember { mutableStateOf(AiModelOption.GEMINI_2_FLASH) }
    var selectedFraming by remember { mutableStateOf(FramingMode.AUTO_9_16) }
    var selectedSafety by remember { mutableStateOf(SafetyActionMode.AUTO_BLEEP) }
    var minDuration by remember { mutableStateOf(15f) }
    var maxDuration by remember { mutableStateOf(45f) }
    var targetClipsCount by remember { mutableStateOf(3) }
    var dynamicCaptionsEnabled by remember { mutableStateOf(true) }
    var selectedHighlightColor by remember { mutableStateOf(SubtitleYellow) }

    var isSubmitting by remember { mutableStateOf(false) }
    var liveStepText by remember { mutableStateOf("جاري التحضير...") }
    var customApiKeyInput by remember { mutableStateOf(GeminiService.getEffectiveApiKey()) }
    var showApiKeyConfig by remember { mutableStateOf(false) }

    val coroutineScope = rememberCoroutineScope()

    // Real Android Photo/Video Picker
    val videoPickerLauncher = rememberLauncherForActivityResult(
        contract = ActivityResultContracts.PickVisualMedia()
    ) { uri: Uri? ->
        if (uri != null) {
            selectedVideoUri = uri
            videoSourceUrl = "فيديو محلي: ${uri.lastPathSegment ?: "video.mp4"}"
        }
    }

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBg)
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 16.dp, bottom = 96.dp)
    ) {
        // Section 1: Header
        item {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "📥 استوديو معالجة وتقطيع الفيديو الحقيقي",
                    style = MaterialTheme.typography.headlineMedium,
                    color = TextPrimary
                )
                Text(
                    text = "استيراد حقيقي لفيديوهات الهاتف أو روابط YouTube، وتحليل الخطافات باستخدام Gemini 2.0 Flash.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
        }

        // Section 2: Real Video Source Picker & URL
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "مصدر الفيديو المستهدف",
                            style = MaterialTheme.typography.titleMedium,
                            color = TextPrimary
                        )

                        // Real video picker button
                        Button(
                            onClick = {
                                videoPickerLauncher.launch(
                                    PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.VideoOnly)
                                )
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = AccentCyan.copy(alpha = 0.2f)),
                            contentPadding = PaddingValues(horizontal = 10.dp, vertical = 4.dp),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.VideoFile, contentDescription = null, tint = AccentCyan, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("اختيار من ذاكرة الهاتف", color = AccentCyan, style = MaterialTheme.typography.labelSmall)
                        }
                    }

                    OutlinedTextField(
                        value = videoSourceUrl,
                        onValueChange = {
                            videoSourceUrl = it
                            selectedVideoUri = null
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag("video_url_input"),
                        placeholder = { Text("أدخل رابط YouTube أو نص التفريغ") },
                        leadingIcon = {
                            Icon(
                                imageVector = if (selectedVideoUri != null) Icons.Default.CheckCircle else Icons.Default.Link,
                                contentDescription = null,
                                tint = if (selectedVideoUri != null) SuccessGreen else PrimaryLight
                            )
                        },
                        trailingIcon = {
                            IconButton(onClick = { videoSourceUrl = "https://youtu.be/arabic_viral_interview" }) {
                                Icon(Icons.Default.ContentPaste, contentDescription = "Paste", tint = AccentCyan)
                            }
                        },
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryLight,
                            unfocusedBorderColor = DarkSurfaceVariant,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        shape = RoundedCornerShape(12.dp)
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        SuggestionChip(
                            onClick = {
                                videoSourceUrl = "النجاح في التجارة الإلكترونية يتطلب فهم العميل أولاً، وتجربة المنتجات الرابحة بسرعة قبل المنافسين."
                            },
                            label = { Text("📝 تجربة نص تفريغ حقيقي") },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextSecondary
                            )
                        )
                        SuggestionChip(
                            onClick = { videoSourceUrl = "https://youtube.com/watch?v=tech_talk_ar" },
                            label = { Text("▶️ بودكاست عربي") },
                            colors = SuggestionChipDefaults.suggestionChipColors(
                                containerColor = DarkSurfaceVariant,
                                labelColor = TextSecondary
                            )
                        )
                    }
                }
            }
        }

        // Section 3: Gemini API Key & Connection Status
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Icon(Icons.Default.VpnKey, contentDescription = null, tint = AccentGold)
                            Text(
                                text = "حالة محرك Gemini AI الحقيقي",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary
                            )
                        }

                        Surface(
                            color = if (GeminiService.hasApiKey()) SuccessGreen.copy(alpha = 0.2f) else AccentGold.copy(alpha = 0.2f),
                            shape = RoundedCornerShape(6.dp)
                        ) {
                            Text(
                                text = if (GeminiService.hasApiKey()) "متصل ومفعل" else "جاهز للاستخدام",
                                style = MaterialTheme.typography.labelSmall,
                                color = if (GeminiService.hasApiKey()) SuccessGreen else AccentGold,
                                modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                            )
                        }
                    }

                    Text(
                        text = if (GeminiService.hasApiKey()) {
                            "يتم إرسال المحتوى مباشرة إلى نموذج Google Gemini 2.0 Flash لاستخراج الخطافات والنصوص بصيغة JSON حقيقية."
                        } else {
                            "يعمل حالياً بالتحليل اللغوي المحلي المدمج. يمكنك إضافة مفتاح Gemini API في أي وقت للحصول على تحليل سحابي متقدم."
                        },
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary
                    )

                    TextButton(
                        onClick = { showApiKeyConfig = !showApiKeyConfig },
                        contentPadding = PaddingValues(0.dp)
                    ) {
                        Text(
                            text = if (showApiKeyConfig) "إخفاء إعداد المفتاح" else "تعديل مفتاح Gemini API...",
                            color = PrimaryLight,
                            style = MaterialTheme.typography.labelMedium
                        )
                    }

                    if (showApiKeyConfig) {
                        OutlinedTextField(
                            value = customApiKeyInput,
                            onValueChange = {
                                customApiKeyInput = it
                                GeminiService.setCustomApiKey(it)
                            },
                            label = { Text("Gemini API Key") },
                            placeholder = { Text("AIzaSy...") },
                            modifier = Modifier.fillMaxWidth(),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor = PrimaryLight,
                                focusedTextColor = TextPrimary,
                                unfocusedTextColor = TextPrimary
                            ),
                            shape = RoundedCornerShape(8.dp)
                        )
                    }
                }
            }
        }

        // Section 4: AI Intelligence Engine Selection
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        text = "محرك استخراج الخطافات الفيروسية",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    AiModelOption.values().forEach { option ->
                        val isSelected = selectedModel == option
                        Surface(
                            onClick = { selectedModel = option },
                            color = if (isSelected) PrimaryIndigo.copy(alpha = 0.15f) else DarkSurfaceVariant,
                            shape = RoundedCornerShape(12.dp),
                            border = if (isSelected) androidx.compose.foundation.BorderStroke(1.5.dp, PrimaryLight) else null,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.SpaceBetween
                            ) {
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    RadioButton(
                                        selected = isSelected,
                                        onClick = { selectedModel = option },
                                        colors = RadioButtonDefaults.colors(selectedColor = PrimaryLight)
                                    )
                                    Column {
                                        Text(
                                            text = option.displayName,
                                            style = MaterialTheme.typography.titleMedium,
                                            color = TextPrimary
                                        )
                                        Text(
                                            text = option.badge,
                                            style = MaterialTheme.typography.bodyMedium,
                                            color = if (isSelected) PrimaryLight else TextSecondary
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Section 5: Camera Direction & Framing
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        text = "توجيه الكاميرا وتتبع الوجوه (Auto-Framing)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    FramingMode.values().forEach { mode ->
                        val isSelected = selectedFraming == mode
                        Surface(
                            onClick = { selectedFraming = mode },
                            color = if (isSelected) AccentCyan.copy(alpha = 0.15f) else DarkSurfaceVariant,
                            shape = RoundedCornerShape(12.dp),
                            border = if (isSelected) androidx.compose.foundation.BorderStroke(1.5.dp, AccentCyan) else null,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier.padding(12.dp),
                                verticalAlignment = Alignment.CenterVertically,
                                horizontalArrangement = Arrangement.spacedBy(12.dp)
                            ) {
                                Icon(
                                    imageVector = when (mode) {
                                        FramingMode.AUTO_9_16 -> Icons.Default.CropPortrait
                                        FramingMode.SPLIT_SCREEN_2_FACES -> Icons.Default.ViewAgenda
                                        FramingMode.ACTIVE_SPEAKER -> Icons.Default.RecordVoiceOver
                                    },
                                    contentDescription = null,
                                    tint = if (isSelected) AccentCyan else TextSecondary
                                )
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(text = mode.title, style = MaterialTheme.typography.titleMedium, color = TextPrimary)
                                    Text(text = mode.desc, style = MaterialTheme.typography.bodyMedium, color = TextSecondary)
                                }
                            }
                        }
                    }
                }
            }
        }

        // Section 6: Dynamic Captions & Hormozi Subtitles
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(text = "الترجمة الديناميكية (نمط هورموزي)", style = MaterialTheme.typography.titleMedium, color = TextPrimary)
                            Text(text = "إبراز الكلمات كلمة بكلمة بالخط العربي عريض", style = MaterialTheme.typography.bodyMedium, color = TextSecondary)
                        }
                        Switch(
                            checked = dynamicCaptionsEnabled,
                            onCheckedChange = { dynamicCaptionsEnabled = it },
                            colors = SwitchDefaults.colors(checkedThumbColor = PrimaryIndigo)
                        )
                    }

                    if (dynamicCaptionsEnabled) {
                        Text(text = "لون إبراز الكلمة النشطة:", style = MaterialTheme.typography.bodyMedium, color = TextSecondary)
                        Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                            listOf(
                                SubtitleYellow to "أصفر",
                                SubtitleNeonGreen to "أخضر نيون",
                                SubtitleCyan to "سماوي",
                                SubtitleCoral to "مرجاني"
                            ).forEach { (color, name) ->
                                val isChosen = selectedHighlightColor == color
                                Box(
                                    modifier = Modifier
                                        .size(36.dp)
                                        .clip(CircleShape)
                                        .background(color)
                                        .clickable { selectedHighlightColor = color }
                                        .border(
                                            width = if (isChosen) 3.dp else 1.dp,
                                            color = if (isChosen) TextPrimary else Color.Transparent,
                                            shape = CircleShape
                                        ),
                                    contentAlignment = Alignment.Center
                                ) {
                                    if (isChosen) {
                                        Icon(Icons.Default.Check, contentDescription = name, tint = DarkBg, modifier = Modifier.size(18.dp))
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }

        // Section 7: Duration Presets & Clips Count
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(text = "طول المقاطع وعددها", style = MaterialTheme.typography.titleMedium, color = TextPrimary)

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf(
                            "⚡ سريع (25s)" to (15f to 25f),
                            "🔥 فيروسي (45s)" to (15f to 45f),
                            "🎙️ بودكاست (60s)" to (30f to 60f),
                            "🎬 مطول (90s)" to (45f to 90f)
                        ).forEach { (presetName, range) ->
                            FilterChip(
                                selected = maxDuration == range.second,
                                onClick = {
                                    minDuration = range.first
                                    maxDuration = range.second
                                },
                                label = { Text(presetName) },
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(text = "العدد المستهدف:", color = TextSecondary)
                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            listOf(2, 3, 5, 8).forEach { count ->
                                FilterChip(
                                    selected = targetClipsCount == count,
                                    onClick = { targetClipsCount = count },
                                    label = { Text("$count مقاطع") }
                                )
                            }
                        }
                    }
                }
            }
        }

        // Section 8: Anti-Strike Safety Filter Gate
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Row(
                        verticalAlignment = Alignment.CenterVertically,
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Icon(Icons.Default.Shield, contentDescription = null, tint = SuccessGreen)
                        Text(text = "فحص الأمان الحقيقي (1,500+ مصطلح في القاموس)", style = MaterialTheme.typography.titleMedium, color = TextPrimary)
                    }

                    SafetyActionMode.values().forEach { mode ->
                        val isSelected = selectedSafety == mode
                        Row(
                            verticalAlignment = Alignment.CenterVertically,
                            modifier = Modifier
                                .fillMaxWidth()
                                .clickable { selectedSafety = mode }
                                .padding(vertical = 4.dp)
                        ) {
                            RadioButton(selected = isSelected, onClick = { selectedSafety = mode })
                            Spacer(modifier = Modifier.width(8.dp))
                            Text(text = mode.label, style = MaterialTheme.typography.bodyMedium, color = TextPrimary)
                        }
                    }
                }
            }
        }

        // Real Execution Button
        item {
            Button(
                onClick = {
                    isSubmitting = true
                    coroutineScope.launch {
                        ProjectRepository.runRealAiVideoCuttingPipeline(
                            sourceUrlOrName = videoSourceUrl,
                            modelOption = selectedModel,
                            framingMode = selectedFraming,
                            safetyMode = selectedSafety,
                            minDuration = minDuration.toInt(),
                            maxDuration = maxDuration.toInt(),
                            targetClipsCount = targetClipsCount
                        ) { step, progress ->
                            liveStepText = step
                        }
                        isSubmitting = false
                        onProcessingStarted()
                    }
                },
                enabled = !isSubmitting,
                colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                shape = RoundedCornerShape(14.dp),
                modifier = Modifier
                    .fillMaxWidth()
                    .height(54.dp)
                    .testTag("start_processing_button")
            ) {
                if (isSubmitting) {
                    CircularProgressIndicator(color = TextPrimary, modifier = Modifier.size(24.dp))
                    Spacer(modifier = Modifier.width(12.dp))
                    Text(liveStepText, style = MaterialTheme.typography.bodyMedium)
                } else {
                    Icon(Icons.Default.Bolt, contentDescription = null, tint = TextPrimary)
                    Spacer(modifier = Modifier.width(8.dp))
                    Text(
                        text = "بدء التقطيع والمونتاج الحقيقي",
                        style = MaterialTheme.typography.titleMedium,
                        fontWeight = FontWeight.Bold,
                        color = TextPrimary
                    )
                }
            }
        }
    }
}
