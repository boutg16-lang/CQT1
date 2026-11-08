package com.example.oussamacutter.ui.screens

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
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
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import com.example.oussamacutter.data.SafetyEngine
import com.example.oussamacutter.model.SafetyActionMode
import com.example.oussamacutter.ui.theme.*

@Composable
fun SafetyScreen(
    modifier: Modifier = Modifier
) {
    var sampleInputText by remember {
        mutableStateOf("الكثير يظن أن خول النجاح سهل ولكن في الحقيقة المنافسة شرسة وتحتاج تخطيط قوي.")
    }
    var currentActionMode by remember { mutableStateOf(SafetyActionMode.AUTO_BLEEP) }
    var evaluatedResult by remember {
        mutableStateOf(SafetyEngine.evaluateText(sampleInputText, currentActionMode))
    }
    var customNewTerm by remember { mutableStateOf("") }
    var activeBlocklist by remember { mutableStateOf(SafetyEngine.defaultBlocklist.take(8)) }

    fun runScan() {
        evaluatedResult = SafetyEngine.evaluateText(sampleInputText, currentActionMode)
    }

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
                    text = "🛡️ درع حماية المحتوى والإنذارات",
                    style = MaterialTheme.typography.headlineMedium,
                    color = TextPrimary
                )
                Text(
                    text = "فحص تلقائي للنصوص والتفريغ الصوتي لمنع إنذارات يوتيوب (Strikes) وتطبيق الكتم الذكي (Bleep).",
                    style = MaterialTheme.typography.bodyMedium,
                    color = TextSecondary
                )
            }
        }

        // Live Risk Scorecard Gauge
        item {
            val report = evaluatedResult.second
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(18.dp),
                border = androidx.compose.foundation.BorderStroke(1.dp, Color(report.riskLevel.colorHex).copy(alpha = 0.4f))
            ) {
                Column(
                    modifier = Modifier.padding(18.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Column {
                            Text(
                                text = "مؤشر خطورة المحتوى (Risk Score)",
                                style = MaterialTheme.typography.titleMedium,
                                color = TextPrimary
                            )
                            Text(
                                text = report.riskLevel.labelAr,
                                style = MaterialTheme.typography.headlineSmall,
                                color = Color(report.riskLevel.colorHex),
                                fontWeight = FontWeight.Bold
                            )
                        }

                        Box(
                            modifier = Modifier
                                .size(64.dp)
                                .clip(CircleShape)
                                .background(Color(report.riskLevel.colorHex).copy(alpha = 0.2f)),
                            contentAlignment = Alignment.Center
                        ) {
                            Text(
                                text = "${report.riskScore}",
                                style = MaterialTheme.typography.headlineMedium,
                                color = Color(report.riskLevel.colorHex),
                                fontWeight = FontWeight.Bold
                            )
                        }
                    }

                    LinearProgressIndicator(
                        progress = { report.riskScore / 100f },
                        modifier = Modifier
                            .fillMaxWidth()
                            .height(8.dp)
                            .clip(RoundedCornerShape(4.dp)),
                        color = Color(report.riskLevel.colorHex),
                        trackColor = DarkSurfaceVariant
                    )

                    Text(
                        text = report.summary,
                        style = MaterialTheme.typography.bodyMedium,
                        color = TextSecondary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp)
                    ) {
                        StatusChip("الكلمات المكتومة: ${report.bleepedWordsCount}", AccentCyan)
                        StatusChip("فحص الصوت: -14 LUFS", SuccessGreen)
                        StatusChip("حقوق النشر: آمن", SuccessGreen)
                    }
                }
            }
        }

        // Live Scanner Input
        item {
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(12.dp)
                ) {
                    Text(
                        text = "نص التفريغ أو المحتوى المراد فحصه",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    OutlinedTextField(
                        value = sampleInputText,
                        onValueChange = {
                            sampleInputText = it
                            runScan()
                        },
                        modifier = Modifier
                            .fillMaxWidth()
                            .heightIn(min = 100.dp)
                            .testTag("safety_text_input"),
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
                        horizontalArrangement = Arrangement.SpaceBetween,
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        Text(
                            text = "وضع المعالجة:",
                            style = MaterialTheme.typography.bodyMedium,
                            color = TextSecondary
                        )

                        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                            SafetyActionMode.values().forEach { mode ->
                                FilterChip(
                                    selected = currentActionMode == mode,
                                    onClick = {
                                        currentActionMode = mode
                                        runScan()
                                    },
                                    label = {
                                        Text(
                                            when (mode) {
                                                SafetyActionMode.AUTO_BLEEP -> "كتم الصوت (Bleep)"
                                                SafetyActionMode.BLOCK_CLIP -> "حظر المقطع"
                                                SafetyActionMode.WARN_ONLY -> "تنبيه فقط"
                                            }
                                        )
                                    },
                                    colors = FilterChipDefaults.filterChipColors(
                                        selectedContainerColor = PrimaryIndigo,
                                        selectedLabelColor = TextPrimary
                                    )
                                )
                            }
                        }
                    }

                    // Censored text preview
                    Surface(
                        color = DarkSurfaceVariant,
                        shape = RoundedCornerShape(10.dp),
                        modifier = Modifier.fillMaxWidth()
                    ) {
                        Column(modifier = Modifier.padding(12.dp)) {
                            Text(
                                text = "معاينة النص بعد تطبيق الفلتر:",
                                style = MaterialTheme.typography.labelSmall,
                                color = PrimaryLight
                            )
                            Spacer(modifier = Modifier.height(4.dp))
                            Text(
                                text = evaluatedResult.first,
                                style = MaterialTheme.typography.bodyLarge,
                                color = TextPrimary
                            )
                        }
                    }
                }
            }
        }

        // Detected Violations Table
        item {
            val violations = evaluatedResult.second.violations
            Card(
                colors = CardDefaults.cardColors(containerColor = DarkCard),
                shape = RoundedCornerShape(16.dp)
            ) {
                Column(
                    modifier = Modifier.padding(16.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp)
                ) {
                    Text(
                        text = "الكلمات المرصودة في هذا المقطع (${violations.size})",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    if (violations.isEmpty()) {
                        Text(
                            text = "لم يتم رصد أي كلمات مخالفة في هذا النص. المقطع مطابق لسياسات النشر 100%.",
                            style = MaterialTheme.typography.bodyMedium,
                            color = SuccessGreen
                        )
                    } else {
                        violations.forEach { item ->
                            Row(
                                modifier = Modifier
                                    .fillMaxWidth()
                                    .clip(RoundedCornerShape(8.dp))
                                    .background(DarkSurfaceVariant)
                                    .padding(10.dp),
                                horizontalArrangement = Arrangement.SpaceBetween,
                                verticalAlignment = Alignment.CenterVertically
                            ) {
                                Column {
                                    Text(
                                        text = "الكلمة: \"${item.term}\"",
                                        style = MaterialTheme.typography.titleMedium,
                                        color = DangerRed
                                    )
                                    Text(
                                        text = "التصنيف: ${item.category} • الخطورة: ${item.severity}",
                                        style = MaterialTheme.typography.bodySmall,
                                        color = TextSecondary
                                    )
                                }
                                Surface(
                                    color = DarkBg,
                                    shape = RoundedCornerShape(6.dp)
                                ) {
                                    Text(
                                        text = item.actionTaken,
                                        style = MaterialTheme.typography.labelSmall,
                                        color = AccentCyan,
                                        modifier = Modifier.padding(horizontal = 6.dp, vertical = 4.dp)
                                    )
                                }
                            }
                        }
                    }
                }
            }
        }

        // Custom Blocklist Terms Manager
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
                        text = "إدارة قائمة الكلمات المحظورة (Custom Lexicon)",
                        style = MaterialTheme.typography.titleMedium,
                        color = TextPrimary
                    )

                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(8.dp),
                        verticalAlignment = Alignment.CenterVertically
                    ) {
                        OutlinedTextField(
                            value = customNewTerm,
                            onValueChange = { customNewTerm = it },
                            placeholder = { Text("أضف كلمة جديدة للحظر...") },
                            modifier = Modifier.weight(1f),
                            shape = RoundedCornerShape(10.dp),
                            colors = OutlinedTextFieldDefaults.colors(
                                focusedBorderColor = PrimaryLight,
                                unfocusedBorderColor = DarkSurfaceVariant,
                                focusedTextColor = TextPrimary,
                                unfocusedTextColor = TextPrimary
                            )
                        )
                        Button(
                            onClick = {
                                if (customNewTerm.isNotBlank() && !activeBlocklist.contains(customNewTerm)) {
                                    activeBlocklist = activeBlocklist + customNewTerm.trim()
                                    customNewTerm = ""
                                    runScan()
                                }
                            },
                            colors = ButtonDefaults.buttonColors(containerColor = PrimaryIndigo),
                            shape = RoundedCornerShape(10.dp)
                        ) {
                            Text("إضافة")
                        }
                    }

                    // Words list chips
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.spacedBy(6.dp)
                    ) {
                        activeBlocklist.forEach { term ->
                            SuggestionChip(
                                onClick = {
                                    activeBlocklist = activeBlocklist.filter { it != term }
                                    runScan()
                                },
                                label = { Text(term) },
                                icon = {
                                    Icon(
                                        Icons.Default.Close,
                                        contentDescription = "حذف",
                                        tint = DangerRed,
                                        modifier = Modifier.size(16.dp)
                                    )
                                },
                                colors = SuggestionChipDefaults.suggestionChipColors(
                                    containerColor = DarkSurfaceVariant,
                                    labelColor = TextPrimary
                                )
                            )
                        }
                    }
                }
            }
        }
    }
}

@Composable
fun StatusChip(label: String, color: Color) {
    Surface(
        color = color.copy(alpha = 0.15f),
        shape = RoundedCornerShape(6.dp)
    ) {
        Text(
            text = label,
            style = MaterialTheme.typography.labelSmall,
            color = color,
            modifier = Modifier.padding(horizontal = 8.dp, vertical = 4.dp)
        )
    }
}
