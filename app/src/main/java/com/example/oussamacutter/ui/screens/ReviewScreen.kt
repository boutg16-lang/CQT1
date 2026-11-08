package com.example.oussamacutter.ui.screens

import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.LazyRow
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.draw.scale
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import android.content.Intent
import androidx.compose.ui.platform.LocalContext
import com.example.oussamacutter.data.ProjectRepository
import com.example.oussamacutter.model.*
import com.example.oussamacutter.ui.theme.*
import kotlinx.coroutines.delay

@OptIn(ExperimentalLayoutApi::class)
@Composable
fun ReviewScreen(
    initialClipId: String? = null,
    onNavigateToPublish: () -> Unit,
    modifier: Modifier = Modifier
) {
    val clips by ProjectRepository.clips.collectAsState()
    val selectedId by ProjectRepository.selectedClipId.collectAsState()
    val currentClip = clips.find { it.id == (initialClipId ?: selectedId) } ?: clips.firstOrNull()

    var isPlaying by remember { mutableStateOf(false) }
    var currentPlayheadMs by remember { mutableLongStateOf(0L) }
    var playbackSpeed by remember { mutableFloatStateOf(1.0f) }

    var editingWord by remember { mutableStateOf<SubtitleWord?>(null) }
    var editedWordText by remember { mutableStateOf("") }
    var showXmlDialog by remember { mutableStateOf(false) }
    var activeStudioTab by remember { mutableStateOf(0) } // 0: Editor, 1: Audio & BGM, 2: Translation & Dubbing

    val totalDurationMs = (currentClip?.durationSeconds ?: 30) * 1000L

    // Playhead simulation ticker
    LaunchedEffect(isPlaying, playbackSpeed) {
        if (isPlaying) {
            while (isPlaying) {
                delay(50)
                currentPlayheadMs += (50 * playbackSpeed).toLong()
                if (currentPlayheadMs >= totalDurationMs) {
                    currentPlayheadMs = 0L
                    isPlaying = false
                }
            }
        }
    }

    // Punch Zoom Effect simulation for first 3 seconds
    val isHookWindow = currentPlayheadMs in 0..3000
    val cameraZoomScale by animateFloatAsState(
        targetValue = if (isHookWindow && (currentClip?.visualEffects?.punchZoomOnHook == true)) 1.08f else 1.0f,
        animationSpec = tween(400),
        label = "PunchZoom"
    )

    LazyColumn(
        modifier = modifier
            .fillMaxSize()
            .background(DarkBg)
            .padding(horizontal = 16.dp),
        verticalArrangement = Arrangement.spacedBy(16.dp),
        contentPadding = PaddingValues(top = 16.dp, bottom = 96.dp)
    ) {
        // Header
        item {
            Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                Text(
                    text = "👀 استوديو المونتاج ومحرر الترجمة",
                    style = MaterialTheme.typography.headlineMedium,
                    color = TextPrimary
                )
                Text(
                    text = "معاينة حية للمقطع 9:16، الترجمة التفاعلية (نمط هورموزي)، هندسة الصوت والترجمة الفورية.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
        }

        // Clips Selector Carousel
        item {
            LazyRow(
                horizontalArrangement = Arrangement.spacedBy(10.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                items(clips) { clip ->
                    val isSelected = clip.id == currentClip?.id
                    Surface(
                        onClick = {
                            ProjectRepository.selectClip(clip.id)
                            currentPlayheadMs = 0L
                            isPlaying = false
                        },
                        color = if (isSelected) PrimaryIndigo else DarkCard,
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.testTag("clip_select_${clip.id}")
                    ) {
                        Row(
                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                            horizontalArrangement = Arrangement.spacedBy(8.dp)
                        ) {
                            Text(
                                text = "مقطع ${clip.id.takeLast(2)}",
                                style = MaterialTheme.typography.labelLarge,
                                color = if (isSelected) TextPrimary else TextSecondary
                            )
                            Surface(
                                color = if (isSelected) DarkBg.copy(alpha = 0.3f) else DarkSurfaceVariant,
                                shape = RoundedCornerShape(6.dp)
                            ) {
                                Text(
                                    text = "${clip.viralScore}%",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = if (isSelected) AccentGold else PrimaryLight,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                )
                            }
                        }
                    }
                }
            }
        }

        if (currentClip != null) {
            // Interactive 9:16 Preview Player Card
            item {
                Card(
                    colors = CardDefaults.cardColors(containerColor = DarkCard),
                    shape = RoundedCornerShape(18.dp)
                ) {
                    Column(
                        modifier = Modifier.padding(16.dp),
                        verticalArrangement = Arrangement.spacedBy(12.dp)
                    ) {
                        // Title & Viral Badge
                        Row(
                            modifier = Modifier.fillMaxWidth(),
                            horizontalArrangement = Arrangement.SpaceBetween,
                            verticalAlignment = Alignment.CenterVertically
                        ) {
                            Column(modifier = Modifier.weight(1f)) {
                                Text(
                                    text = currentClip.arabicHookTitle,
                                    style = MaterialTheme.typography.titleLarge,
                                    color = TextPrimary
                                )
                                Text(
                                    text = currentClip.title,
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = TextSecondary
                                )
                            }

                            Surface(
                                color = AccentGold.copy(alpha = 0.2f),
                                shape = RoundedCornerShape(8.dp)
                            ) {
                                Row(
                                    modifier = Modifier.padding(horizontal = 10.dp, vertical = 6.dp),
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(4.dp)
                                ) {
                                    Icon(Icons.Default.LocalFireDepartment, contentDescription = null, tint = AccentGold, modifier = Modifier.size(18.dp))
                                    Text(
                                        text = "${currentClip.viralScore}",
                                        style = MaterialTheme.typography.titleMedium,
                                        color = AccentGold,
                                        fontWeight = FontWeight.Bold
                                    )
                                }
                            }
                        }

                        // Video Player Screen
                        val previewHeight = when (currentClip.visualEffects.aspectRatio) {
                            "9:16" -> 280.dp
                            "1:1" -> 220.dp
                            else -> 180.dp
                        }

                        Box(
                            modifier = Modifier
                                .fillMaxWidth()
                                .height(previewHeight)
                                .clip(RoundedCornerShape(14.dp))
                                .background(Color(0xFF070B14))
                                .border(1.dp, PrimaryLight.copy(alpha = 0.25f), RoundedCornerShape(14.dp))
                                .scale(cameraZoomScale),
                            contentAlignment = Alignment.Center
                        ) {
                            // Top Story / Shorts Progress Bar
                            if (currentClip.visualEffects.topProgressBar) {
                                LinearProgressIndicator(
                                    progress = { (currentPlayheadMs.toFloat() / totalDurationMs).coerceIn(0f, 1f) },
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .height(3.dp)
                                        .align(Alignment.TopCenter),
                                    color = PrimaryLight,
                                    trackColor = Color.White.copy(alpha = 0.2f)
                                )
                            }

                            // Face Detection Simulation Bounding Box
                            Box(
                                modifier = Modifier
                                    .size(110.dp, 140.dp)
                                    .border(1.5.dp, AccentCyan.copy(alpha = 0.6f), RoundedCornerShape(12.dp))
                                    .align(Alignment.Center)
                            ) {
                                Text(
                                    text = "🎯 المتحدث (Auto 9:16)",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = AccentCyan,
                                    modifier = Modifier
                                        .align(Alignment.TopCenter)
                                        .padding(4.dp)
                                )
                            }

                            // Active Subtitle Word Detection
                            val activeWord = currentClip.words.find { word ->
                                currentPlayheadMs in word.startMs..word.endMs
                            } ?: currentClip.words.firstOrNull()

                            // Dynamic Hormozi Live Caption Display
                            Column(
                                modifier = Modifier
                                    .align(Alignment.BottomCenter)
                                    .padding(bottom = 20.dp, start = 16.dp, end = 16.dp),
                                horizontalAlignment = Alignment.CenterHorizontally
                            ) {
                                if (activeWord != null) {
                                    val isBleep = activeWord.isBleeped
                                    Surface(
                                        color = if (isBleep) DangerRed.copy(alpha = 0.85f) else Color.Black.copy(alpha = 0.75f),
                                        shape = RoundedCornerShape(8.dp)
                                    ) {
                                        Text(
                                            text = if (isBleep) "🔇 [BLEEPED]" else activeWord.text,
                                            style = MaterialTheme.typography.headlineMedium,
                                            color = if (isBleep) TextPrimary else SubtitleYellow,
                                            fontWeight = FontWeight.ExtraBold,
                                            modifier = Modifier.padding(horizontal = 12.dp, vertical = 6.dp)
                                        )
                                    }
                                }

                                // Dual Caption translation preview if active
                                if (currentClip.translation.isDualCaptions) {
                                    Text(
                                        text = currentClip.translation.translatedTitle,
                                        style = MaterialTheme.typography.bodySmall,
                                        color = AccentCyan,
                                        fontWeight = FontWeight.Bold,
                                        modifier = Modifier.padding(top = 4.dp)
                                    )
                                }
                            }

                            // Safe Area Margins Tag
                            Surface(
                                color = DarkBg.copy(alpha = 0.8f),
                                shape = RoundedCornerShape(6.dp),
                                modifier = Modifier
                                    .align(Alignment.TopStart)
                                    .padding(8.dp)
                            ) {
                                Text(
                                    text = "${currentClip.visualEffects.aspectRatio} Safe Zone",
                                    style = MaterialTheme.typography.labelSmall,
                                    color = TextSecondary,
                                    modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                )
                            }

                            // Hook Alert Banner at 0-3s
                            if (isHookWindow) {
                                Surface(
                                    color = AccentGold.copy(alpha = 0.9f),
                                    shape = RoundedCornerShape(6.dp),
                                    modifier = Modifier
                                        .align(Alignment.TopEnd)
                                        .padding(8.dp)
                                ) {
                                    Text(
                                        text = "⚡ HOOK PUNCH ZOOM",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = DarkBg,
                                        fontWeight = FontWeight.Bold,
                                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                    )
                                }
                            }
                        }

                        // Playback Controller & Scrubber Bar
                        Column(verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Slider(
                                value = currentPlayheadMs.toFloat(),
                                onValueChange = {
                                    currentPlayheadMs = it.toLong()
                                },
                                valueRange = 0f..totalDurationMs.toFloat(),
                                colors = SliderDefaults.colors(
                                    thumbColor = PrimaryLight,
                                    activeTrackColor = PrimaryIndigo
                                )
                            )

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                val currentSec = (currentPlayheadMs / 1000).toInt()
                                val totalSec = currentClip.durationSeconds
                                Text(
                                    text = "00:${currentSec.toString().padStart(2, '0')} / 00:${totalSec.toString().padStart(2, '0')}",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = TextSecondary
                                )

                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                                ) {
                                    IconButton(
                                        onClick = {
                                            currentPlayheadMs = (currentPlayheadMs - 5000).coerceAtLeast(0L)
                                        }
                                    ) {
                                        Icon(Icons.Default.Replay5, contentDescription = "Rewind", tint = TextPrimary)
                                    }

                                    IconButton(
                                        onClick = { isPlaying = !isPlaying },
                                        modifier = Modifier
                                            .size(44.dp)
                                            .clip(CircleShape)
                                            .background(PrimaryIndigo)
                                            .testTag("play_pause_button")
                                    ) {
                                        Icon(
                                            imageVector = if (isPlaying) Icons.Default.Pause else Icons.Default.PlayArrow,
                                            contentDescription = "Play/Pause",
                                            tint = TextPrimary
                                        )
                                    }

                                    IconButton(
                                        onClick = {
                                            currentPlayheadMs = (currentPlayheadMs + 5000).coerceAtMost(totalDurationMs)
                                        }
                                    ) {
                                        Icon(Icons.Default.Forward5, contentDescription = "Forward", tint = TextPrimary)
                                    }

                                    // Speed chip
                                    FilterChip(
                                        selected = playbackSpeed > 1.0f,
                                        onClick = {
                                            playbackSpeed = when (playbackSpeed) {
                                                1.0f -> 1.25f
                                                1.25f -> 1.5f
                                                else -> 1.0f
                                            }
                                        },
                                        label = { Text("${playbackSpeed}x") }
                                    )
                                }

                                Text(
                                    text = "🔊 ${currentClip.audioLoudnessLUFS} LUFS",
                                    style = MaterialTheme.typography.bodyMedium,
                                    color = SuccessGreen
                                )
                            }
                        }
                    }
                }
            }

            // Aspect Ratio Quick Toggle Row
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(8.dp)
                ) {
                    listOf("9:16" to "تيك توك وريلز (9:16)", "1:1" to "انستغرام فيد (1:1)", "16:9" to "يوتيوب أفقي (16:9)").forEach { (ratio, label) ->
                        val isSelected = currentClip.visualEffects.aspectRatio == ratio
                        FilterChip(
                            selected = isSelected,
                            onClick = {
                                ProjectRepository.updateVisualEffects(
                                    currentClip.id,
                                    currentClip.visualEffects.copy(aspectRatio = ratio)
                                )
                            },
                            label = { Text(label) },
                            colors = FilterChipDefaults.filterChipColors(
                                selectedContainerColor = PrimaryIndigo,
                                selectedLabelColor = TextPrimary
                            ),
                            modifier = Modifier.weight(1f)
                        )
                    }
                }
            }

            // Studio Sub-Tabs
            item {
                ScrollableTabRow(
                    selectedTabIndex = activeStudioTab,
                    containerColor = DarkCard,
                    contentColor = PrimaryLight,
                    edgePadding = 8.dp
                ) {
                    Tab(
                        selected = activeStudioTab == 0,
                        onClick = { activeStudioTab = 0 },
                        text = { Text("✍️ محرر الكلمات") }
                    )
                    Tab(
                        selected = activeStudioTab == 1,
                        onClick = { activeStudioTab = 1 },
                        text = { Text("🎵 الصوت وBGM") }
                    )
                    Tab(
                        selected = activeStudioTab == 2,
                        onClick = { activeStudioTab = 2 },
                        text = { Text("🌍 الترجمة والدبلجة") }
                    )
                    Tab(
                        selected = activeStudioTab == 3,
                        onClick = { activeStudioTab = 3 },
                        text = { Text("📈 الاستبقاء وB-Roll") }
                    )
                    Tab(
                        selected = activeStudioTab == 4,
                        onClick = { activeStudioTab = 4 },
                        text = { Text("🖼️ أغلفة الشورتس") }
                    )
                }
            }

            // Tab 0: Word-by-Word Subtitle Editor
            if (activeStudioTab == 0) {
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
                                    text = "تعديل الكلمات وكتم الصوت (انقر للتعديل)",
                                    style = MaterialTheme.typography.titleMedium,
                                    color = TextPrimary
                                )
                                Icon(Icons.Default.Edit, contentDescription = null, tint = PrimaryLight)
                            }

                            FlowRow(
                                horizontalArrangement = Arrangement.spacedBy(8.dp),
                                verticalArrangement = Arrangement.spacedBy(8.dp),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                currentClip.words.forEach { word ->
                                    val isCurrentWord = currentPlayheadMs in word.startMs..word.endMs
                                    Surface(
                                        onClick = {
                                            editingWord = word
                                            editedWordText = word.text
                                        },
                                        color = when {
                                            word.isBleeped -> DangerRed.copy(alpha = 0.25f)
                                            isCurrentWord -> SubtitleYellow.copy(alpha = 0.3f)
                                            word.isHookKeyword -> PrimaryIndigo.copy(alpha = 0.2f)
                                            else -> DarkSurfaceVariant
                                        },
                                        shape = RoundedCornerShape(8.dp),
                                        border = if (word.isBleeped) androidx.compose.foundation.BorderStroke(1.dp, DangerRed)
                                        else if (isCurrentWord) androidx.compose.foundation.BorderStroke(1.5.dp, SubtitleYellow)
                                        else null
                                    ) {
                                        Row(
                                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 6.dp),
                                            verticalAlignment = Alignment.CenterVertically,
                                            horizontalArrangement = Arrangement.spacedBy(4.dp)
                                        ) {
                                            Text(
                                                text = if (word.isBleeped) "***" else word.text,
                                                style = MaterialTheme.typography.bodyMedium,
                                                color = if (word.isBleeped) DangerRed else TextPrimary,
                                                fontWeight = FontWeight.SemiBold
                                            )
                                            if (word.isBleeped) {
                                                Icon(Icons.Default.VolumeOff, contentDescription = null, tint = DangerRed, modifier = Modifier.size(14.dp))
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Tab 1: Audio QC & BGM Mixer Panel
            if (activeStudioTab == 1) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(14.dp)
                        ) {
                            Text(
                                text = "ميكسر الصوت والموسيقى الخلفية (BGM Mixer)",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary
                            )

                            // BGM selection
                            Text(text = "الموسيقى الخلفية المدمجة:", style = MaterialTheme.typography.bodySmall, color = TextSecondary)
                            ProjectRepository.availableBgmList.forEach { bgm ->
                                val isSelected = currentClip.audioSettings.selectedBgm == bgm
                                Row(
                                    verticalAlignment = Alignment.CenterVertically,
                                    modifier = Modifier
                                        .fillMaxWidth()
                                        .clickable {
                                            ProjectRepository.updateAudioSettings(
                                                currentClip.id,
                                                currentClip.audioSettings.copy(selectedBgm = bgm)
                                            )
                                        }
                                ) {
                                    RadioButton(
                                        selected = isSelected,
                                        onClick = {
                                            ProjectRepository.updateAudioSettings(
                                                currentClip.id,
                                                currentClip.audioSettings.copy(selectedBgm = bgm)
                                            )
                                        },
                                        colors = RadioButtonDefaults.colors(selectedColor = PrimaryLight)
                                    )
                                    Text(text = bgm, color = TextPrimary, style = MaterialTheme.typography.bodyMedium)
                                }
                            }

                            // Volume slider & Auto Ducking
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("خفت الموسيقى عند الكلام (Auto-Ducking):", color = TextPrimary)
                                Switch(
                                    checked = currentClip.audioSettings.autoDucking,
                                    onCheckedChange = {
                                        ProjectRepository.updateAudioSettings(
                                            currentClip.id,
                                            currentClip.audioSettings.copy(autoDucking = it)
                                        )
                                    },
                                    colors = SwitchDefaults.colors(checkedThumbColor = SuccessGreen)
                                )
                            }

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("المؤثرات الصوتية للخطاف (Whoosh / Pop SFX):", color = TextPrimary)
                                Switch(
                                    checked = currentClip.audioSettings.whooshSfx,
                                    onCheckedChange = {
                                        ProjectRepository.updateAudioSettings(
                                            currentClip.id,
                                            currentClip.audioSettings.copy(whooshSfx = it)
                                        )
                                    },
                                    colors = SwitchDefaults.colors(checkedThumbColor = AccentCyan)
                                )
                            }
                        }
                    }
                }
            }

            // Tab 2: Translation & Dubbing Hub
            if (activeStudioTab == 2) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(14.dp)
                        ) {
                            Text(
                                text = "الترجمة التلقائية والدبلجة الصوتية (AI Dubbing)",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary
                            )

                            Text("اللغة المستهدفة للترجمة:", style = MaterialTheme.typography.bodySmall, color = TextSecondary)
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                listOf("English", "French", "Spanish", "Turkish").forEach { lang ->
                                    val isSelected = currentClip.translation.targetLang == lang
                                    FilterChip(
                                        selected = isSelected,
                                        onClick = {
                                            ProjectRepository.updateTranslation(
                                                currentClip.id,
                                                currentClip.translation.copy(targetLang = lang)
                                            )
                                        },
                                        label = { Text(lang) },
                                        modifier = Modifier.weight(1f)
                                    )
                                }
                            }

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Text("إظهار سطر ترجمة ثنائي (Dual Captions):", color = TextPrimary)
                                Switch(
                                    checked = currentClip.translation.isDualCaptions,
                                    onCheckedChange = {
                                        ProjectRepository.updateTranslation(
                                            currentClip.id,
                                            currentClip.translation.copy(isDualCaptions = it)
                                        )
                                    },
                                    colors = SwitchDefaults.colors(checkedThumbColor = PrimaryIndigo)
                                )
                            }

                            Surface(
                                color = DarkSurfaceVariant,
                                shape = RoundedCornerShape(10.dp),
                                modifier = Modifier.fillMaxWidth()
                            ) {
                                Column(modifier = Modifier.padding(12.dp)) {
                                    Text("العنوان المترجم المقترح:", color = TextSecondary, style = MaterialTheme.typography.labelSmall)
                                    Spacer(modifier = Modifier.height(4.dp))
                                    Text(
                                        text = currentClip.translation.translatedTitle,
                                        color = TextPrimary,
                                        style = MaterialTheme.typography.titleSmall
                                    )
                                }
                            }
                        }
                    }
                }
            }

            // Tab 3: Retention Curve & AI B-Roll Overlays
            if (activeStudioTab == 3) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(14.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column {
                                    Text(
                                        text = "📈 منحنى استبقاء المشاهدين التنبؤي",
                                        style = MaterialTheme.typography.titleMedium,
                                        color = TextPrimary
                                    )
                                    Text(
                                        text = "متوسط المشاهدة المتوقع: 84% • أداء فوق المعدل",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = SuccessGreen
                                    )
                                }
                                Surface(
                                    color = PrimaryIndigo.copy(alpha = 0.2f),
                                    shape = RoundedCornerShape(8.dp)
                                ) {
                                    Text(
                                        text = "Retention AI",
                                        style = MaterialTheme.typography.labelSmall,
                                        color = PrimaryLight,
                                        modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                                    )
                                }
                            }

                            // Retention Points Curve Bar
                            Column(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                                currentClip.retentionCurve.forEach { point ->
                                    Row(
                                        modifier = Modifier.fillMaxWidth(),
                                        verticalAlignment = Alignment.CenterVertically,
                                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                                    ) {
                                        Text(
                                            text = "00:${point.second.toString().padStart(2, '0')}",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = TextSecondary,
                                            modifier = Modifier.width(42.dp)
                                        )

                                        LinearProgressIndicator(
                                            progress = { point.retentionPercent / 100f },
                                            modifier = Modifier
                                                .weight(1f)
                                                .height(8.dp)
                                                .clip(RoundedCornerShape(4.dp)),
                                            color = when {
                                                point.retentionPercent >= 85 -> SuccessGreen
                                                point.retentionPercent >= 75 -> AccentCyan
                                                else -> AccentGold
                                            },
                                            trackColor = DarkSurfaceVariant
                                        )

                                        Text(
                                            text = "${point.retentionPercent}%",
                                            style = MaterialTheme.typography.bodySmall,
                                            color = TextPrimary,
                                            fontWeight = FontWeight.Bold,
                                            modifier = Modifier.width(36.dp)
                                        )

                                        Text(
                                            text = point.annotation,
                                            style = MaterialTheme.typography.labelSmall,
                                            color = TextSecondary,
                                            maxLines = 1,
                                            modifier = Modifier.width(130.dp)
                                        )
                                    }
                                }
                            }

                            HorizontalDivider(color = DarkSurfaceVariant)

                            // AI B-Roll Suggestions Section
                            Text(
                                text = "🎬 مشاهد الـ B-Roll المقترحة لرفع الاستبقاء:",
                                style = MaterialTheme.typography.titleSmall,
                                color = TextPrimary
                            )

                            currentClip.bRollSuggestions.forEach { bRoll ->
                                Surface(
                                    color = DarkSurfaceVariant,
                                    shape = RoundedCornerShape(10.dp),
                                    modifier = Modifier.fillMaxWidth()
                                ) {
                                    Row(
                                        modifier = Modifier.padding(12.dp),
                                        verticalAlignment = Alignment.CenterVertically,
                                        horizontalArrangement = Arrangement.spacedBy(10.dp)
                                    ) {
                                        Box(
                                            modifier = Modifier
                                                .size(38.dp)
                                                .clip(RoundedCornerShape(8.dp))
                                                .background(AccentCyan.copy(alpha = 0.2f)),
                                            contentAlignment = Alignment.Center
                                        ) {
                                            Icon(Icons.Default.MovieCreation, contentDescription = null, tint = AccentCyan)
                                        }

                                        Column(modifier = Modifier.weight(1f)) {
                                            Text(
                                                text = bRoll.clipTitle,
                                                style = MaterialTheme.typography.titleSmall,
                                                color = TextPrimary
                                            )
                                            Text(
                                                text = "كلمة الربط: \"${bRoll.keyword}\" • التوقيت: 00:${(bRoll.timestampMs / 1000).toInt().toString().padStart(2, '0')}",
                                                style = MaterialTheme.typography.bodySmall,
                                                color = TextSecondary
                                            )
                                        }

                                        Switch(
                                            checked = bRoll.isApplied,
                                            onCheckedChange = {
                                                ProjectRepository.toggleBRollApplied(currentClip.id, bRoll.id)
                                            },
                                            colors = SwitchDefaults.colors(checkedThumbColor = AccentCyan)
                                        )
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Tab 4: High-CTR Shorts Thumbnails Studio
            if (activeStudioTab == 4) {
                item {
                    Card(
                        colors = CardDefaults.cardColors(containerColor = DarkCard),
                        shape = RoundedCornerShape(16.dp)
                    ) {
                        Column(
                            modifier = Modifier.padding(16.dp),
                            verticalArrangement = Arrangement.spacedBy(14.dp)
                        ) {
                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column {
                                    Text(
                                        text = "🖼️ مولد أغلفة الشورتس (Shorts Thumbnails)",
                                        style = MaterialTheme.typography.titleMedium,
                                        color = TextPrimary
                                    )
                                    Text(
                                        text = "تم التقاط 3 إطارات ذات تعبيرات عاطفية عالية ونسبة نقر متوقعة.",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = TextSecondary
                                    )
                                }
                            }

                            Row(
                                modifier = Modifier.fillMaxWidth(),
                                horizontalArrangement = Arrangement.spacedBy(10.dp)
                            ) {
                                currentClip.thumbnails.forEach { thumb ->
                                    val isChosen = thumb.isSelected
                                    Surface(
                                        onClick = { ProjectRepository.selectThumbnail(currentClip.id, thumb.id) },
                                        color = if (isChosen) PrimaryIndigo.copy(alpha = 0.2f) else DarkSurfaceVariant,
                                        shape = RoundedCornerShape(12.dp),
                                        border = if (isChosen) androidx.compose.foundation.BorderStroke(2.dp, PrimaryLight) else null,
                                        modifier = Modifier.weight(1f)
                                    ) {
                                        Column(
                                            modifier = Modifier.padding(10.dp),
                                            horizontalAlignment = Alignment.CenterHorizontally,
                                            verticalArrangement = Arrangement.spacedBy(6.dp)
                                        ) {
                                            // Mockup 9:16 frame container
                                            Box(
                                                modifier = Modifier
                                                    .fillMaxWidth()
                                                    .height(110.dp)
                                                    .clip(RoundedCornerShape(8.dp))
                                                    .background(Color(0xFF0F172A)),
                                                contentAlignment = Alignment.Center
                                            ) {
                                                Text(
                                                    text = thumb.badgeEmoji,
                                                    style = MaterialTheme.typography.headlineMedium
                                                )
                                                Surface(
                                                    color = Color.Black.copy(alpha = 0.7f),
                                                    shape = RoundedCornerShape(4.dp),
                                                    modifier = Modifier
                                                        .align(Alignment.BottomCenter)
                                                        .padding(bottom = 6.dp)
                                                ) {
                                                    Text(
                                                        text = thumb.catchyText,
                                                        style = MaterialTheme.typography.labelSmall,
                                                        color = SubtitleYellow,
                                                        fontWeight = FontWeight.Bold,
                                                        modifier = Modifier.padding(horizontal = 4.dp, vertical = 2.dp)
                                                    )
                                                }
                                            }

                                            Text(
                                                text = "CTR: ${thumb.ctrPredictionPercent}%",
                                                style = MaterialTheme.typography.bodySmall,
                                                color = SuccessGreen,
                                                fontWeight = FontWeight.Bold
                                            )

                                            Button(
                                                onClick = { ProjectRepository.selectThumbnail(currentClip.id, thumb.id) },
                                                colors = ButtonDefaults.buttonColors(
                                                    containerColor = if (isChosen) PrimaryIndigo else DarkBg
                                                ),
                                                shape = RoundedCornerShape(6.dp),
                                                modifier = Modifier.fillMaxWidth(),
                                                contentPadding = PaddingValues(vertical = 4.dp)
                                            ) {
                                                Text(
                                                    text = if (isChosen) "الغلاف النشط" else "اختيار",
                                                    style = MaterialTheme.typography.labelSmall,
                                                    color = TextPrimary
                                                )
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // Export & Publishing Action Row
            item {
                Row(
                    modifier = Modifier.fillMaxWidth(),
                    horizontalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    OutlinedButton(
                        onClick = { showXmlDialog = true },
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentCyan),
                        border = androidx.compose.foundation.BorderStroke(1.dp, AccentCyan),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                            .testTag("export_xml_button")
                    ) {
                        Icon(Icons.Default.FileDownload, contentDescription = null)
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("تصدير Premiere XML")
                    }

                    Button(
                        onClick = onNavigateToPublish,
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier
                            .weight(1f)
                            .height(48.dp)
                            .testTag("go_publish_button")
                    ) {
                        Icon(Icons.Default.RocketLaunch, contentDescription = null)
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("نشر وجدولة المقطع")
                    }
                }
            }
        }
    }

    // Edit Word Modal Dialog
    if (editingWord != null) {
        val word = editingWord!!
        AlertDialog(
            onDismissRequest = { editingWord = null },
            title = { Text("تعديل الكلمة والتحكم الصوتي", color = TextPrimary) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                    OutlinedTextField(
                        value = editedWordText,
                        onValueChange = { editedWordText = it },
                        label = { Text("نص الكلمة") },
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryLight,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        )
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("كتم الصوت (Bleep + ***):", color = TextPrimary)
                        Switch(
                            checked = word.isBleeped,
                            onCheckedChange = { isBleep ->
                                if (currentClip != null) {
                                    ProjectRepository.updateWord(currentClip.id, word.id, editedWordText, isBleep)
                                    editingWord = word.copy(isBleeped = isBleep)
                                }
                            },
                            colors = SwitchDefaults.colors(checkedThumbColor = DangerRed)
                        )
                    }
                }
            },
            confirmButton = {
                Button(
                    onClick = {
                        if (currentClip != null) {
                            ProjectRepository.updateWord(currentClip.id, word.id, editedWordText, word.isBleeped)
                        }
                        editingWord = null
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo)
                ) {
                    Text("حفظ التعديل")
                }
            },
            dismissButton = {
                TextButton(onClick = { editingWord = null }) {
                    Text("إلغاء", color = TextSecondary)
                }
            },
            containerColor = DarkCard
        )
    }

    // Export XML & SRT Modal Dialog
    if (showXmlDialog && currentClip != null) {
        val context = LocalContext.current
        AlertDialog(
            onDismissRequest = { showXmlDialog = false },
            title = { Text("تصدير ومشاركة ملفات المونتاج", color = TextPrimary) },
            text = {
                Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                    Text(
                        text = "تسلسل زمني XML متوافق مع Adobe Premiere Pro و Final Cut Pro بدقة ${currentClip.visualEffects.aspectRatio} ومعدل 30fps، بالإضافة لملف الترجمة SRT الكامل.",
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary
                    )
                    Surface(
                        color = DarkBg,
                        shape = RoundedCornerShape(8.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(max = 140.dp)
                    ) {
                        Text(
                            text = ProjectRepository.generatePremiereProXml(currentClip).take(260) + "\n...",
                            style = MaterialTheme.typography.bodySmall,
                            color = AccentCyan,
                            modifier = Modifier.padding(8.dp)
                        )
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        Button(
                            onClick = {
                                val xmlContent = ProjectRepository.generatePremiereProXml(currentClip)
                                val intent = Intent(Intent.ACTION_SEND).apply {
                                    type = "text/plain"
                                    putExtra(Intent.EXTRA_TEXT, xmlContent)
                                    putExtra(Intent.EXTRA_TITLE, "${currentClip.title}_Premiere.xml")
                                }
                                context.startActivity(Intent.createChooser(intent, "مشاركة Premiere XML"))
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                            modifier = Modifier.weight(1f),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.Share, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("مشاركة XML", style = MaterialTheme.typography.labelSmall)
                        }

                        OutlinedButton(
                            onClick = {
                                val srtContent = ProjectRepository.generateSrtSubtitles(currentClip)
                                val intent = Intent(Intent.ACTION_SEND).apply {
                                    type = "text/plain"
                                    putExtra(Intent.EXTRA_TEXT, srtContent)
                                    putExtra(Intent.EXTRA_TITLE, "${currentClip.title}.srt")
                                }
                                context.startActivity(Intent.createChooser(intent, "مشاركة ملف SRT"))
                            },
                            colors = ButtonDefaults.outlinedButtonColors(contentColor = AccentCyan),
                            border = androidx.compose.foundation.BorderStroke(1.dp, AccentCyan),
                            modifier = Modifier.weight(1f),
                            shape = RoundedCornerShape(8.dp)
                        ) {
                            Icon(Icons.Default.Subtitles, contentDescription = null, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("مشاركة SRT", style = MaterialTheme.typography.labelSmall)
                        }
                    }
                }
            },
            confirmButton = {
                TextButton(onClick = { showXmlDialog = false }) {
                    Text("إغلاق", color = TextSecondary)
                }
            },
            containerColor = DarkCard
        )
    }
}
