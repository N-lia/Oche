package ai.oyi.device.ui.theme

import android.content.Context
import android.os.Build
import androidx.compose.animation.core.FiniteAnimationSpec
import androidx.compose.animation.core.spring
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.ColorScheme
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Shapes
import androidx.compose.material3.Typography
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.dynamicDarkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.CompositionLocalProvider
import androidx.compose.runtime.staticCompositionLocalOf
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

/**
 * OYI's Material 3 Expressive theme.
 *
 * Expressive in four ways: springy motion ([OyiMotion]), a wider shape scale
 * with fully round pills and big soft cards, heavier display type, and an
 * optional Material You palette that follows the wallpaper. The brand stays
 * pitch black with a warm orange accent unless the user picks wallpaper
 * colours.
 */
@Composable
fun OyiTheme(
    wallpaperColors: Boolean = false,
    content: @Composable () -> Unit,
) {
    val context = LocalContext.current
    val colors = oyiColorScheme(context, wallpaperColors)
    CompositionLocalProvider(LocalOyiAccent provides colors.primary) {
        MaterialTheme(
            colorScheme = colors,
            typography = OyiTypography,
            shapes = OyiShapes,
            content = content,
        )
    }
}

/** The brand palette, or Material You colours on the brand's black. */
fun oyiColorScheme(context: Context, wallpaperColors: Boolean): ColorScheme {
    if (wallpaperColors && Build.VERSION.SDK_INT >= Build.VERSION_CODES.S) {
        val dynamic = dynamicDarkColorScheme(context)
        return dynamic.copy(
            background = BrandScheme.background,
            onBackground = BrandScheme.onBackground,
            surface = BrandScheme.surface,
        )
    }
    return BrandScheme
}

/** The accent the island and the glass pick up (brand orange or wallpaper). */
val LocalOyiAccent = staticCompositionLocalOf { Color(0xFFF08A65) }

private val BrandScheme = darkColorScheme(
    primary = Color(0xFFF08A65),
    onPrimary = Color(0xFF0A0807),
    primaryContainer = Color(0xFFD9724E),
    onPrimaryContainer = Color(0xFF0A0807),
    secondary = Color(0xFFF7EDD0),
    onSecondary = Color(0xFF0B0A08),
    secondaryContainer = Color(0xFFE9DCBA),
    onSecondaryContainer = Color(0xFF0B0A08),
    tertiary = Color(0xFFF7E27E),
    onTertiary = Color(0xFF0B0A08),
    background = Color(0xFF070708),
    onBackground = Color(0xFFF8F6F1),
    surface = Color(0xFF151518),
    onSurface = Color(0xFFF8F6F1),
    surfaceVariant = Color(0xFF1F1F24),
    onSurfaceVariant = Color(0xFFA09C94),
    error = Color(0xFFF08A65),
    onError = Color(0xFF0A0807),
    errorContainer = Color(0xFF2E1B14),
    onErrorContainer = Color(0xFFF7EDD0),
    outline = Color(0xFF38383E),
)

/** Expressive type: big, heavy headlines and tight, confident labels. */
val OyiTypography = Typography(
    displayLarge = TextStyle(fontSize = 57.sp, lineHeight = 60.sp, fontWeight = FontWeight.Black, letterSpacing = (-1.2).sp),
    displayMedium = TextStyle(fontSize = 44.sp, lineHeight = 48.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.9).sp),
    displaySmall = TextStyle(fontSize = 36.sp, lineHeight = 40.sp, fontWeight = FontWeight.ExtraBold, letterSpacing = (-0.6).sp),
    headlineLarge = TextStyle(fontSize = 32.sp, lineHeight = 36.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.4).sp),
    headlineMedium = TextStyle(fontSize = 26.sp, lineHeight = 30.sp, fontWeight = FontWeight.Bold, letterSpacing = (-0.2).sp),
    headlineSmall = TextStyle(fontSize = 22.sp, lineHeight = 26.sp, fontWeight = FontWeight.Bold),
    titleLarge = TextStyle(fontSize = 20.sp, lineHeight = 24.sp, fontWeight = FontWeight.Bold),
    titleMedium = TextStyle(fontSize = 16.sp, lineHeight = 20.sp, fontWeight = FontWeight.SemiBold),
    titleSmall = TextStyle(fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.SemiBold),
    bodyLarge = TextStyle(fontSize = 16.sp, lineHeight = 22.sp),
    bodyMedium = TextStyle(fontSize = 14.sp, lineHeight = 20.sp),
    bodySmall = TextStyle(fontSize = 12.sp, lineHeight = 16.sp),
    labelLarge = TextStyle(fontSize = 14.sp, lineHeight = 18.sp, fontWeight = FontWeight.Bold),
    labelMedium = TextStyle(fontSize = 12.sp, lineHeight = 16.sp, fontWeight = FontWeight.Bold),
    labelSmall = TextStyle(fontSize = 10.5.sp, lineHeight = 14.sp, fontWeight = FontWeight.Bold, letterSpacing = 0.8.sp),
)

/** The expressive shape scale: soft cards, very round containers. */
val OyiShapes = Shapes(
    extraSmall = RoundedCornerShape(8.dp),
    small = RoundedCornerShape(12.dp),
    medium = RoundedCornerShape(20.dp),
    large = RoundedCornerShape(28.dp),
    extraLarge = RoundedCornerShape(36.dp),
)

/**
 * Material 3 Expressive motion tokens as springs.
 *
 * Spatial springs move and resize things and are allowed to overshoot;
 * effects springs fade and recolour and never bounce. Values follow the
 * expressive motion scheme.
 */
object OyiMotion {
    fun <T> fastSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.6f, stiffness = 800f)
    fun <T> defaultSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 380f)
    fun <T> slowSpatial(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.8f, stiffness = 200f)
    fun <T> fastEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 3800f)
    fun <T> defaultEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 1600f)
    fun <T> slowEffects(): FiniteAnimationSpec<T> = spring(dampingRatio = 1f, stiffness = 800f)

    /** A playful pop for stickers and confirmations. */
    fun <T> bouncy(): FiniteAnimationSpec<T> = spring(dampingRatio = 0.45f, stiffness = 500f)
}
