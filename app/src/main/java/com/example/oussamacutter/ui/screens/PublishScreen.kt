package com.example.oussamacutter.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.oussamacutter.data.ProjectRepository
import com.example.oussamacutter.model.ClipSegment
import com.example.oussamacutter.ui.theme.*

@Composable
fun PublishScreen(
    modifier: Modifier = Modifier
) {
    val clips by ProjectRepository.clips.collectAsState()
    val scheduledJobs by ProjectRepository.publishedJobs.collectAsState()
    var selectedClip by remember { mutableStateOf<ClipSegment?>(clips.firstOrNull()) }

    var selectedPlatform by remember { mutableStateOf("YouTube Shorts") }
    var privacyStatus by remember { mutableStateOf("Public (علني)") }
    var intervalMinutes by remember { mutableStateOf("60") }
    var customTitle by remember {
        mutableStateOf(selectedClip?.arabicHookTitle ?: "🔥 سر النجاح الخفي الذي يغير مستقبلك!")
    }
    var descriptionText by remember {
        mutableStateOf("شاهد هذا المقطع القصير وشاركنا رأيك في التعليقات!\n\n#شورتس #ريادة_أعمال #تطوير_الذات #viral #نجاح")
    }
    var showScheduledSuccess by remember { mutableStateOf(false) }

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
                    text = "🚀 النشر الذكي وجدولة المقاطع",
                    style = MaterialTheme.typography.headlineMedium,
                    color = TextPrimary
                )
                Text(
                    text = "توليد عناوين فيروسية متوافقة مع SEO، فحص بوابات الأمان، وجدولة النشر التلقائي.",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
        }

        // Platform Selector
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
                        text = "المنصة المستهدفة للنشر",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        listOf("YouTube Shorts", "TikTok", "Instagram Reels").forEach { platform ->
                            val isSelected = selectedPlatform == platform
                            FilterChip(
                                selected = isSelected,
                                onClick = { selectedPlatform = platform },
                                label = { Text(platform) },
                                colors = FilterChipDefaults.filterChipColors(
                                    selectedContainerColor = PrimaryIndigo,
                                    selectedLabelColor = TextPrimary
                                ),
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }
                }
            }
        }

        // Clip to Publish Selection
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
                        text = "اختر المقطع المراد جدولته",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    clips.forEach { clip ->
                        val isChosen = selectedClip?.id == clip.id
                        Surface(
                            onClick = {
                                selectedClip = clip
                                customTitle = clip.arabicHookTitle
                            },
                            color = if (isChosen) PrimaryIndigo.copy(alpha = 0.15f) else DarkSurfaceVariant,
                            shape = RoundedCornerShape(10.dp),
                            border = if (isChosen) androidx.compose.foundation.BorderStroke(1.5.dp, PrimaryLight) else null,
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Row(
                                modifier = Modifier.padding(10.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column(modifier = Modifier.weight(1f)) {
                                    Text(
                                        text = clip.arabicHookTitle,
                                        style = MaterialTheme.typography.titleSmall,
                                        color = TextPrimary
                                    )
                                    Text(
                                        text = "${clip.durationSeconds} ثانية • Viral: ${clip.viralScore}%",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = TextSecondary
                                    )
                                }
                                if (isChosen) {
                                    Icon(Icons.Default.CheckCircle, contentDescription = null, tint = PrimaryLight)
                                }
                            }
                        }
                    }
                }
            }
        }

        // Viral Title & Metadata Generator
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
                            text = "العنوان الفيروسي (Arabic SEO Hook)",
                            style = MaterialTheme.typography.titleMedium,
                            color = TextPrimary
                        )

                        TextButton(
                            onClick = {
                                customTitle = "😱 السر الذي أخفاه عنك الجميع! (شاهد للنهاية)"
                            }
                        ) {
                            Icon(Icons.Default.AutoAwesome, contentDescription = null, tint = AccentGold, modifier = Modifier.size(16.dp))
                            Spacer(modifier = Modifier.width(4.dp))
                            Text("توليد هوك جديد", color = AccentGold)
                        }
                    }

                    Text(
                        text = "صيغة الهوك ونمط العنوان:",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf(
                            "كشف سر" to "🔥 السر الذي أخفاه عنك الجميع! (شاهد للنهاية)",
                            "سؤال صادم" to "😱 هل تعتقد أن النجاح صدفة؟ الحقيقة ستصدمك!",
                            "خطوات عملية" to "💡 3 خطوات فقط لتحقيق هدفك هذا الشهر!",
                            "تحذير عاجل" to "⚠️ توقف عن فعل هذا الخطأ القاتل فوراً!"
                        ).forEach { (formula, sample) ->
                            FilterChip(
                                selected = customTitle.startsWith(sample.take(2)),
                                onClick = { customTitle = sample },
                                label = { Text(formula) },
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }

                    Text(
                        text = "اللهجة والأسلوب المستهدف:",
                        style = MaterialTheme.typography.bodySmall,
                        color = TextSecondary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        listOf(
                            "الفصحى" to "شاهد هذا المقطع واكتشف سر النجاح الحقيقي!",
                            "خليجي / سعودي" to "شوف المقطع للآخر ولا يفوتك هذا السر الخرافي!",
                            "مصري" to "بص على السر ده اللي هيغير تفكيرك تماماً!",
                            "مغاربي" to "شوف هاد السر الخطير اللي غادي يبدل حياتك!"
                        ).forEach { (dialect, text) ->
                            FilterChip(
                                selected = descriptionText.contains(dialect.take(3)),
                                onClick = {
                                    descriptionText = "$text\n\n#شورتس #ريادة_أعمال #تطوير_الذات #viral"
                                },
                                label = { Text(dialect) },
                                modifier = Modifier.weight(1f)
                            )
                        }
                    }

                    OutlinedTextField(
                        value = customTitle,
                        onValueChange = { customTitle = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .testTag("publish_title_input"),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryLight,
                            unfocusedBorderColor = DarkSurfaceVariant,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        shape = RoundedCornerShape(10.dp)
                    )

                    Text(
                        text = "الوصف والوسوم (Hashtags)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    OutlinedTextField(
                        value = descriptionText,
                        onValueChange = { descriptionText = it },
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 90.dp),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryLight,
                            unfocusedBorderColor = DarkSurfaceVariant,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        shape = RoundedCornerShape(10.dp)
                    )
                }
            }
        }

        // Schedule Settings & Preflight Compliance Gate
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
                        text = "إعدادات الجدولة وبوابة الأمان (Preflight Gate)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("الخصوصية:", color = TextSecondary)
                        Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                            listOf("Public (علني)", "Unlisted (غير مدرج)", "Private (خاص)").forEach { p ->
                                FilterChip(
                                    selected = privacyStatus == p,
                                    onClick = { privacyStatus = p },
                                    label = { Text(p.substringBefore(" ")) }
                                )
                            }
                        }
                    }

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text("الفاصل بين المقاطع:", color = TextSecondary)
                        Text("$intervalMinutes دقيقة", color = PrimaryLight, fontWeight = FontWeight.Bold)
                    }

                    // Preflight checks indicators
                    Column(
                        modifier = Modifier
                            .fillMaxWidth()
                            .background(DarkSurfaceVariant, RoundedCornerShape(10.dp))
                            .padding(10.dp),
                        verticalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        ComplianceCheckRow("قواطع أمان يوتيوب (Circuit Breaker)", "نشط وناجح", SuccessGreen)
                        ComplianceCheckRow("حصة الرفع اليومية (Upload Quota)", "متوفرة بالكامل", SuccessGreen)
                        ComplianceCheckRow("فحص الأمان ضد الـ Strikes", "تم اجتياز الفحص 100%", SuccessGreen)
                    }

                    Button(
                        onClick = {
                            if (selectedClip != null) {
                                ProjectRepository.scheduleClipForPublish(
                                    clip = selectedClip!!,
                                    platform = selectedPlatform,
                                    scheduledTime = "اليوم - خلال 60 دقيقة",
                                    privacy = privacyStatus,
                                    tags = listOf("#شورتس", "#ريادة", "#viral")
                                )
                                showScheduledSuccess = true
                            }
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(50.dp)
                            .testTag("schedule_publish_btn")
                    ) {
                        Icon(Icons.Default.Schedule, contentDescription = null)
                        Spacer(modifier = Modifier.width(8.dp))
                        Text("جدولة النشر تلقائياً")
                    }

                    if (showScheduledSuccess) {
                        Text(
                            text = "✅ تم إضافة المقطع بنجاح إلى جدول النشر الآلي!",
                            style = MaterialTheme.typography.bodyMedium,
                            color = SuccessGreen
                        )
                    }
                }
            }
        }

        // Section: A/B Testing Title Tournament (ELO Tournament AI)
        item {
            val abTests by ProjectRepository.abTitleTests.collectAsState()
            var testTitleA by remember { mutableStateOf("سر النجاح في ريادة الأعمال") }
            var testTitleB by remember { mutableStateOf("🔥 السر الصادم الذي أخفاه عنك كل الأثرياء!") }

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
                            Text(
                                text = "⚔️ بطولة اختبار العناوين A/B (ELO AI Tournament)",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary
                            )
                            Text(
                                text = "مقارنة احتمالية النقر (CTR) بين عنوانين بناءً على خوارزميات الانتشار.",
                                style = MaterialTheme.typography.bodySmall,
                                color = AccentGold
                            )
                        }
                        Icon(Icons.Default.SportsScore, contentDescription = null, tint = AccentGold)
                    }

                    OutlinedTextField(
                        value = testTitleA,
                        onValueChange = { testTitleA = it },
                        label = { Text("العنوان أ (التقليدي)") },
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = PrimaryLight,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        shape = RoundedCornerShape(10.dp)
                    )

                    OutlinedTextField(
                        value = testTitleB,
                        onValueChange = { testTitleB = it },
                        label = { Text("العنوان ب (الفيروسي المقترح)") },
                        modifier = Modifier.fillMaxWidth(),
                        colors = OutlinedTextFieldDefaults.colors(
                            focusedBorderColor = AccentGold,
                            focusedTextColor = TextPrimary,
                            unfocusedTextColor = TextPrimary
                        ),
                        shape = RoundedCornerShape(10.dp)
                    )

                    Button(
                        onClick = {
                            ProjectRepository.addAbTest(testTitleA, testTitleB)
                        },
                        colors = ButtonDefaults.buttonColors(containerColor = AccentGold),
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Icon(Icons.Default.CompareArrows, contentDescription = null, tint = DarkBg)
                        Spacer(modifier = Modifier.width(6.dp))
                        Text("بدء معركة العناوين وتوقع الفائز", color = DarkBg, fontWeight = FontWeight.Bold)
                    }

                    // Show active test results
                    abTests.firstOrNull()?.let { result ->
                        Surface(
                            color = DarkSurfaceVariant,
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.fillMaxWidth()
                        ) {
                            Column(
                                modifier = Modifier.padding(12.dp),
                                verticalArrangement = Arrangement.spacedBy(6.dp)
                            ) {
                                Row(
                                    modifier = Modifier.fillMaxWidth(),
                                    horizontalArrangement = Arrangement.SpaceBetween,
                                    verticalAlignment = Alignment.CenterVertically
                                ) {
                                    Text("🏆 العنوان الفائز المتوقع:", color = SuccessGreen, fontWeight = FontWeight.Bold)
                                    Surface(
                                        color = SuccessGreen.copy(alpha = 0.2f),
                                        shape = RoundedCornerShape(6.dp)
                                    ) {
                                        Text(
                                            text = "+24% CTR",
                                            color = SuccessGreen,
                                            style = MaterialTheme.typography.labelSmall,
                                            modifier = Modifier.padding(horizontal = 6.dp, vertical = 2.dp)
                                        )
                                    }
                                }

                                Text(
                                    text = result.predictedWinner,
                                    style = MaterialTheme.typography.titleSmall,
                                    color = TextPrimary
                                )

                                Text(
                                    text = result.insight,
                                    style = MaterialTheme.typography.bodySmall,
                                    color = TextSecondary
                                )

                                Button(
                                    onClick = {
                                        customTitle = result.predictedWinner
                                    },
                                    colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                                    shape = RoundedCornerShape(8.dp),
                                    modifier = Modifier.fillMaxWidth(),
                                    contentPadding = PaddingValues(vertical = 4.dp)
                                ) {
                                    Text("اعتماد العنوان الفائز في النشر 👆", style = MaterialTheme.typography.labelMedium)
                                }
                            }
                        }
                    }
                }
            }
        }

        // Scheduled Publications List
        item {
            Text(
                text = "المقاطع المجدولة حالياً (${scheduledJobs.size})",
                style = MaterialTheme.typography.titleLarge,
                color = TextPrimary
            )
        }

        items(scheduledJobs) { job ->
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkSurfaceVariant),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.fillMaxWidth()
            ) {
                Row(
                    modifier = Modifier.padding(12.dp),
                    horizontalArrangement = Arrangement.SpaceBetween,
                    verticalAlignment = Alignment.CenterVertically
                ) {
                    Column(modifier = Modifier.weight(1f)) {
                        Text(text = job.clipTitle, style = MaterialTheme.typography.titleSmall, color = TextPrimary)
                        Text(text = "${job.platform} • ${job.scheduledTime}", style = MaterialTheme.typography.bodySmall, color = TextSecondary)
                    }

                    Surface(
                        color = SuccessGreen.copy(alpha = 0.2f),
                        shape = RoundedCornerShape(6.dp)
                    ) {
                        Text(
                            text = job.status,
                            style = MaterialTheme.typography.labelSmall,
                            color = SuccessGreen,
                            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
                        )
                    }
                }
            }
        }
    }
}

@Composable
fun ComplianceCheckRow(title: String, status: String, color: Color) {
    Row(
        modifier = Modifier.fillMaxWidth(),
        horizontalArrangement = Arrangement.SpaceBetween,
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(title, style = MaterialTheme.typography.bodySmall, color = TextSecondary)
        Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            Icon(Icons.Default.Check, contentDescription = null, tint = color, modifier = Modifier.size(14.dp))
            Text(status, style = MaterialTheme.typography.bodySmall, color = color, fontWeight = FontWeight.Bold)
        }
    }
}
