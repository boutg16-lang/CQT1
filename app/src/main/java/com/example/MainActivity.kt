package com.example

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.compose.animation.AnimatedContent
import androidx.compose.animation.fadeIn
import androidx.compose.animation.fadeOut
import androidx.compose.animation.togetherWith
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.vector.ImageVector
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.unit.dp
import com.example.oussamacutter.ui.screens.*
import com.example.oussamacutter.ui.theme.*

enum class AppScreen(val title: String, val icon: ImageVector) {
    HOME("الرئيسية", Icons.Default.Home),
    CREATE("إنشاء مقاطع", Icons.Default.AddCircle),
    SAFETY("حامي المحتوى", Icons.Default.Shield),
    REVIEW("مراجعة وترجمة", Icons.Default.Visibility),
    PUBLISH("النشر والجدولة", Icons.Default.RocketLaunch)
}

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge()
        com.example.oussamacutter.data.SafetyEngine.initializeFromAssets(this)

        setContent {
            OussamaCutterTheme {
                MainApp()
            }
        }
    }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MainApp() {
    var currentScreen by remember { mutableStateOf(AppScreen.HOME) }
    val screenStack = remember { mutableStateListOf(AppScreen.HOME) }

    fun navigateTo(screen: AppScreen) {
        if (screen != currentScreen) {
            screenStack.add(screen)
            currentScreen = screen
        }
    }

    BackHandler(enabled = screenStack.size > 1) {
        screenStack.removeAt(screenStack.size - 1)
        currentScreen = screenStack.lastOrNull() ?: AppScreen.HOME
    }

    Scaffold(
        modifier = Modifier.fillMaxSize(),
        topBar = {
            TopAppBar(
                title = {
                    Row(
                        modifier = Modifier.fillMaxWidth(),
                        horizontalArrangement = Arrangement.SpaceBetween
                    ) {
                        Text(
                            text = "OUSSAMA Cutter",
                            style = MaterialTheme.typography.titleLarge,
                            color = TextPrimary
                        )
                    }
                },
                colors = TopAppBarDefaults.topAppBarColors(
                    containerColor = DarkBg,
                    titleContentColor = TextPrimary
                ),
                windowInsets = WindowInsets.statusBars
            )
        },
        bottomBar = {
            NavigationBar(
                containerColor = DarkSurface,
                contentColor = TextPrimary,
                modifier = Modifier
                    .fillMaxWidth()
                    .windowInsetsPadding(WindowInsets.navigationBars)
                    .testTag("main_bottom_nav")
            ) {
                AppScreen.values().forEach { screen ->
                    val isSelected = currentScreen == screen
                    NavigationBarItem(
                        selected = isSelected,
                        onClick = { navigateTo(screen) },
                        icon = {
                            Icon(
                                imageVector = screen.icon,
                                contentDescription = screen.title
                            )
                        },
                        label = {
                            Text(
                                text = screen.title,
                                style = MaterialTheme.typography.bodySmall,
                                maxLines = 1
                            )
                        },
                        colors = NavigationBarItemDefaults.colors(
                            selectedIconColor = TextPrimary,
                            selectedTextColor = PrimaryLight,
                            indicatorColor = PrimaryIndigo,
                            unselectedIconColor = TextSecondary,
                            unselectedTextColor = TextSecondary
                        ),
                        modifier = Modifier.testTag("nav_${screen.name.lowercase()}")
                    )
                }
            }
        },
        contentWindowInsets = WindowInsets.safeDrawing
    ) { innerPadding ->
        Box(
            modifier = Modifier
                .fillMaxSize()
                .padding(innerPadding)
                .background(DarkBg)
        ) {
            AnimatedContent(
                targetState = currentScreen,
                transitionSpec = { fadeIn() togetherWith fadeOut() },
                label = "ScreenTransition"
            ) { target ->
                when (target) {
                    AppScreen.HOME -> HomeScreen(
                        onNavigateToCreate = { navigateTo(AppScreen.CREATE) },
                        onNavigateToSafety = { navigateTo(AppScreen.SAFETY) },
                        onNavigateToReview = { _ -> navigateTo(AppScreen.REVIEW) },
                        onNavigateToPublish = { navigateTo(AppScreen.PUBLISH) }
                    )
                    AppScreen.CREATE -> CreateScreen(
                        onProcessingStarted = { navigateTo(AppScreen.REVIEW) }
                    )
                    AppScreen.SAFETY -> SafetyScreen()
                    AppScreen.REVIEW -> ReviewScreen(
                        onNavigateToPublish = { navigateTo(AppScreen.PUBLISH) }
                    )
                    AppScreen.PUBLISH -> PublishScreen()
                }
            }
        }
    }
}
