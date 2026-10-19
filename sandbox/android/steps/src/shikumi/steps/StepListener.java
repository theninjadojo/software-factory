package shikumi.steps;

// Step screenshots for a demo video of an Android app. docs/videos.md, docs/workers.md (Android demo steps)
//
// Lives in the factory-android image, never in the project. android-run.sh (emulator mode, FACTORY_STEPS=1) adds this folder to every
// Android module's androidTest sources through a Gradle init script and names this class as a listener of the test runner, so the
// app's own tests run unchanged. It needs only what every AndroidJUnitRunner project has (JUnit 4, androidx.test); everything else is
// reached by reflection, so a project without it still builds and runs, with no steps.
//
// Espresso (3.5 and newer) reports what it does as tracing spans (androidx.test.platform.tracing): "Espresso.onView(...)" on the test
// thread when a test starts an interaction, then "Espresso.perform(TypeTextAction, ...)" on the main thread around each action. When
// an action ends, a background thread takes a frame every FRAME_MS until the screen has had SETTLE_MS to settle; the test thread waits
// for that at its next interaction (like the web recipe's slowMo), and the step is the last frame, its clip the frames before it that
// differ (a screen sliding in). Actions with no interaction between them (perform(typeText(...), closeSoftKeyboard())) make one step,
// named after the first that types, clicks or presses. Where it touched comes from the activity's own touch events (its window
// callback is wrapped; nothing else changes); the field a value was typed into, and where the value appeared, from that text view.
// The label is the action and what it acted on (a button's text, a field's hint or id), never a typed value. Each test's first step
// is the screen it starts on ("Open <title>"), so the next one has a picture to start from. A last action that no interaction follows
// is kept as the screen was before the activity closed. Jetpack Compose tests do not go through Espresso actions: they get no steps.
//
// Writes through the shell (UiAutomation runs as the shell user) to OUT on the emulator, which the recipe pulls after the run:
//   <name>.png, zz-<name>-fN.png   the steps and their clip frames, the long side at most MAX_SIDE px
//   steps.tsv     <name>.png TAB <n> TAB <label> TAB <motion JSON>   as worker/recipes/trace-steps.mjs writes it
//   shots.tsv     <name>.png TAB <class> TAB <method>              which test took each step
//   results.tsv   <class> TAB <method> TAB pass|fail TAB <message>
import android.app.Activity;
import android.app.Application;
import android.app.Instrumentation;
import android.app.UiAutomation;
import android.graphics.Bitmap;
import android.os.Bundle;
import android.os.Looper;
import android.os.ParcelFileDescriptor;
import android.os.SystemClock;
import android.text.Layout;
import android.util.Log;
import android.view.MotionEvent;
import android.view.View;
import android.view.ViewGroup;
import android.view.Window;
import android.widget.CompoundButton;
import android.widget.EditText;
import android.widget.TextView;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.lang.ref.WeakReference;
import java.lang.reflect.InvocationHandler;
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;
import java.util.concurrent.Future;
import java.util.zip.CRC32;
import org.junit.runner.Description;
import org.junit.runner.Result;
import org.junit.runner.notification.Failure;
import org.junit.runner.notification.RunListener;

public final class StepListener extends RunListener {
  static final String TAG = "shikumi-steps";
  static final String OUT = "/data/local/tmp/shikumi-steps";
  static final int SETTLE_MS = 700, FRAME_MS = 100, OPEN_MS = 300, LEAD_MS = 300, STILL_MS = 3000, MAX_SIDE = 1600;
  static final int MAX_STEPS = 20, MAX_TOTAL = 120, MAX_CLIP = 6, MAX_CLIP_TOTAL = 150, MAX_CLIP_MS = 10_000, MAX_FRAMES = 12;

  /** One Espresso action, as seen on the main thread when it ended. */
  static final class Action {
    String span, act, target = "";
    long start, end;
    float x = -1, y = -1;
    WeakReference<TextView> field;
  }

  /** A frame the background thread took, ms after the uptime clock's zero. */
  static final class Frame {
    final long at;
    final Bitmap b;
    Frame(long at, Bitmap b) { this.at = at; this.b = b; }
  }

  private Instrumentation ins;
  private UiAutomation ui;
  private final ExecutorService shooter = Executors.newSingleThreadExecutor();
  private final List<Action> pending = new ArrayList<>();
  private Future<List<Frame>> shooting;
  private volatile long settleUntil;
  private volatile Activity resumed;
  private volatile long pausedAt;
  private volatile float downX = -1, downY = -1;
  private volatile long downAt;
  private volatile String downName = "";
  private volatile boolean downToggle;
  private volatile int rawWidth;
  private String cls = "", method = "", head = "";
  private boolean failed, ready;
  private String failure = "";
  private int n, total, clips;
  private Bitmap last;
  private final StringBuilder steps = new StringBuilder(), shots = new StringBuilder(), results = new StringBuilder();

  @Override public void testRunStarted(Description d) {
    try {
      ins = (Instrumentation) Class.forName("androidx.test.platform.app.InstrumentationRegistry").getMethod("getInstrumentation").invoke(null);
      ui = ins.getUiAutomation();
      Application app = (Application) ins.getTargetContext().getApplicationContext();
      ins.runOnMainSync(() -> app.registerActivityLifecycleCallbacks(new Watch()));
      Class<?> tracing = Class.forName("androidx.test.platform.tracing.Tracing");
      Class<?> tracer = Class.forName("androidx.test.platform.tracing.Tracer");
      Object t = Proxy.newProxyInstance(getClass().getClassLoader(), new Class<?>[] {tracer}, (p, m, a) -> {
        if (m.getName().equals("beginSpan") && a != null && a.length == 1) return span(String.valueOf(a[0]));
        return plain(p, m, a);
      });
      Object inst = tracing.getMethod("getInstance").invoke(null);
      tracing.getMethod("registerTracer", tracer).invoke(inst, t);
      ready = true;
      Log.i(TAG, "recording steps");
    } catch (Throwable e) {             // an older Espresso (no tracing) or no androidx.test: the tests still run, with no steps
      Log.w(TAG, "no steps: " + e);
    }
  }

  @Override public void testStarted(Description d) {
    cls = String.valueOf(d.getClassName());
    method = String.valueOf(d.getMethodName());
    String full = slug(cls.substring(cls.lastIndexOf('.') + 1) + "-" + method);
    if (full.length() > 40) full = full.substring(0, 18) + "-" + full.substring(full.length() - 21);
    CRC32 c = new CRC32();
    c.update((cls + "#" + method).getBytes(StandardCharsets.UTF_8));
    head = full + "-s%02d-" + String.format(Locale.ROOT, "%06x", c.getValue() & 0xffffff);
    synchronized (pending) {
      pending.clear();
    }
    n = 0;
    last = null;
    failed = false;
    failure = "";
  }

  @Override public void testFailure(Failure f) {
    failed = true;
    failure = oneLine(String.valueOf(f.getMessage()), 300);
  }

  @Override public void testAssumptionFailure(Failure f) {
    testFailure(f);
  }

  @Override public void testFinished(Description d) {
    flush(true);                         // the last action, when no interaction came after it
    results.append(clean(cls)).append('\t').append(clean(method)).append('\t').append(failed ? "fail" : "pass").append('\t').append(failure).append('\n');
    write("steps.tsv", steps.toString().getBytes(StandardCharsets.UTF_8));
    write("shots.tsv", shots.toString().getBytes(StandardCharsets.UTF_8));
    write("results.tsv", results.toString().getBytes(StandardCharsets.UTF_8));
  }

  @Override public void testRunFinished(Result r) {
    shooter.shutdownNow();
  }

  // ---- Espresso's spans

  private Object span(String name) {
    final long start = SystemClock.uptimeMillis();
    final String act = ready ? action(name) : null;
    Log.d(TAG, "span " + name);
    if (ready && (name.startsWith("Espresso.onView") || name.startsWith("Espresso.onData")) && Looper.myLooper() != Looper.getMainLooper()) {
      flush(false);                      // the test thread starts its next interaction: the step before it is kept first
      if (n == 0) open();
    }
    InvocationHandler h = (p, m, a) -> {
      if (m.getName().equals("beginChildSpan") && a != null && a.length == 1) return span(String.valueOf(a[0]));
      if (m.getName().equals("close")) {
        if (act != null) ended(name, act, start);
        return null;
      }
      return plain(p, m, a);
    };
    try {
      Class<?> s = Class.forName("androidx.test.platform.tracing.Tracer$Span");
      return Proxy.newProxyInstance(getClass().getClassLoader(), new Class<?>[] {s}, h);
    } catch (ClassNotFoundException e) {
      return null;
    }
  }

  private static Object plain(Object p, Method m, Object[] a) {
    switch (m.getName()) {
      case "hashCode": return System.identityHashCode(p);
      case "equals": return a != null && a.length == 1 && a[0] == p;
      case "toString": return "shikumi-steps";
      default: return null;
    }
  }

  // The kind of step an Espresso span is (click, fill, press, other), or null when it is not an action on the screen. Espresso names
  // the span after the call and the action's class: "Espresso.perform(GeneralClickAction, view.getId() is 2131230801)".
  static String action(String span) {
    if (!span.startsWith("Espresso.perform(")) return null;
    String c = span.substring("Espresso.perform(".length()).split("[,)]", 2)[0].toLowerCase(Locale.ROOT);
    if (c.contains("typetext") || c.contains("replacetext") || c.contains("cleartext")) return "fill";
    if (c.contains("click") || c.contains("tap")) return "click";
    if (c.contains("pressback") || c.contains("keyevent") || c.contains("editoraction") || c.contains("presskey")) return "press";
    return "other";
  }

  // "Close keyboard" from "Espresso.perform(CloseKeyboardAction, ...)": the action's class in words, for a step with no target.
  static String verb(String span) {
    int i = span.indexOf('(');
    String c = (i >= 0 ? span.substring(i + 1) : span).split("[,)\\s]", 2)[0];
    c = c.substring(c.lastIndexOf('.') + 1).replaceAll("(View)?Action$", "");
    if (c.isEmpty()) return "Act";
    String w = c.replaceAll("([a-z])([A-Z])", "$1 $2").toLowerCase(Locale.ROOT);
    return Character.toUpperCase(w.charAt(0)) + w.substring(1);
  }

  // On the main thread, as an action's span closes: what it did and where, and frames start being taken while the screen settles.
  private void ended(String name, String act, long start) {
    Action a = new Action();
    a.span = name;
    a.act = act;
    a.start = start;
    a.end = SystemClock.uptimeMillis();
    if (downX >= 0 && downAt >= start) {
      a.x = downX;
      a.y = downY;
      a.target = downName;
      if (downToggle && act.equals("click")) a.act = "check";
    }
    if (act.equals("fill")) {
      Activity r = resumed;
      View f = r == null ? null : r.getCurrentFocus();
      if (f instanceof TextView) {
        a.field = new WeakReference<>((TextView) f);
        a.target = describe(f);
      }
    }
    downX = -1;
    settleUntil = a.end + SETTLE_MS;
    synchronized (pending) {
      pending.add(a);
      if (shooting == null) shooting = shooter.submit(this::settle);
    }
  }

  // The background thread: frames until the screen has had SETTLE_MS since the last action and stopped changing (two frames alike),
  // or STILL_MS more have gone by (something that never stops moving); each frame different from the one before.
  private List<Frame> settle() {
    List<Frame> got = new ArrayList<>();
    Bitmap prev = null;
    while (true) {
      long now = SystemClock.uptimeMillis();
      Bitmap b = shot();
      boolean same = b != null && prev != null && b.sameAs(prev);
      if (b != null && !same) {
        if (got.size() >= MAX_FRAMES) got.remove(got.size() / 2);
        got.add(new Frame(now, b));
        prev = b;
      }
      if (now >= settleUntil && (same || now >= settleUntil + STILL_MS)) return got;
      SystemClock.sleep(FRAME_MS);
    }
  }

  // The screen once it has stopped changing (the activity's own opening animation done), at most STILL_MS from now; null without one.
  private Bitmap still() {
    long until = SystemClock.uptimeMillis() + STILL_MS;
    Bitmap prev = shot();
    while (prev != null && SystemClock.uptimeMillis() < until) {
      SystemClock.sleep(FRAME_MS);
      Bitmap b = shot();
      if (b == null || b.sameAs(prev)) return prev;
      prev = b;
    }
    return prev;
  }

  // On the test thread: keep the step the pending actions made, once the screen has settled. At the end of a test (closing) the frames
  // taken after its activity closed are left out.
  private void flush(boolean closing) {
    List<Action> acts;
    Future<List<Frame>> f;
    synchronized (pending) {
      acts = new ArrayList<>(pending);
      pending.clear();
      f = shooting;
      shooting = null;
    }
    if (f == null || acts.isEmpty()) return;
    List<Frame> frames;
    try {
      frames = f.get();
    } catch (Exception e) {
      return;
    }
    if (closing && resumed == null) {
      long cut = pausedAt;
      frames.removeIf(x -> x.at > cut);
    }
    if (frames.isEmpty() || !room()) return;
    Action main = acts.get(0);
    for (Action a : acts) {
      if (!a.act.equals("other")) {
        main = a;
        break;
      }
    }
    Bitmap b = frames.get(frames.size() - 1).b;
    if (last != null && b.sameAs(last)) return;                // nothing visible changed: not a step
    float k = rawWidth > 0 ? b.getWidth() / (float) rawWidth : 1f;      // screen pixels (touches, views) to the kept picture's
    StringBuilder m = new StringBuilder("{\"act\":\"").append(main.act).append('"');
    if (main.x >= 0) m.append(",\"x\":").append(Math.round(main.x * k)).append(",\"y\":").append(Math.round(main.y * k));
    if (main.field != null && main.field.get() != null) {
      int[][] fb = boxes(main.field.get());
      if (fb != null) {
        if (fb[1] != null) m.append(",\"box\":").append(box(fb[1], k));
        m.append(",\"field\":").append(box(fb[0], k));
      }
    }
    long t0 = Math.max(main.start, main.end - LEAD_MS);           // Espresso's wait before that shows nothing new: not replayed
    long end = frames.get(frames.size() - 1).at - t0;
    List<Frame> clip = new ArrayList<>();
    if (!main.act.equals("fill") && last != null && end <= MAX_CLIP_MS) {
      for (Frame x : frames.subList(0, frames.size() - 1)) if (!x.b.sameAs(last)) clip.add(x);
      if (clip.size() > MAX_CLIP) {                               // spread over the time, always ending with the last
        List<Frame> c = new ArrayList<>();
        for (int i = 0; i < MAX_CLIP; i++) c.add(clip.get(Math.round((i + 1) * clip.size() / (float) MAX_CLIP) - 1));
        clip = c;
      }
      while (clips + clip.size() > MAX_CLIP_TOTAL && !clip.isEmpty()) clip.remove(0);
    }
    m.append('}');
    String verb = main.act.equals("click") ? "Click" : main.act.equals("check") ? "Check" : main.act.equals("fill") ? "Fill" : verb(main.span);
    keep(b, verb + (main.target.isEmpty() ? "" : " " + main.target), m.toString(), clip, t0, end);
  }

  private boolean room() {
    return ready && total < MAX_TOTAL && n < MAX_STEPS;
  }

  // A test's first step: the screen it starts on, before its first interaction.
  private void open() {
    if (!room()) return;
    ins.waitForIdleSync();
    SystemClock.sleep(OPEN_MS);
    Bitmap b = still();
    if (b == null) return;
    Activity a = resumed;
    String title = a == null ? "" : a.getTitle() != null && a.getTitle().length() > 0 ? a.getTitle().toString() : a.getClass().getSimpleName();
    keep(b, "Open " + oneLine(title, 80), "{\"act\":\"other\"}", new ArrayList<>(), 0, 0);
  }

  private void keep(Bitmap b, String label, String motion, List<Frame> clip, long start, long end) {
    String name = String.format(Locale.ROOT, head, n + 1);
    if (!write(name + ".png", png(b))) return;
    n++;
    total++;
    last = b;
    String m = motion;
    StringBuilder c = new StringBuilder();
    int kept = 0;
    for (Frame x : clip) {
      long at = x.at - start;
      String f = "zz-" + name + "-f" + (kept + 1);
      if (at < 0 || at > end || !write(f + ".png", png(x.b))) continue;
      c.append(kept == 0 ? "" : ",").append('[').append(at).append(",\"").append(f).append("\"]");
      kept++;
    }
    clips += kept;
    if (kept > 0) m = motion.substring(0, motion.length() - 1) + ",\"clip\":[" + c + "],\"end\":" + end + "}";
    Log.i(TAG, "step " + name + ": " + label + (kept > 0 ? " (" + kept + " frames)" : ""));
    steps.append(name).append(".png\t").append(n).append('\t').append(oneLine(label, 160)).append('\t').append(m).append('\n');
    shots.append(name).append(".png\t").append(clean(cls)).append('\t').append(clean(method)).append('\n');
  }

  // ---- the screen

  private Bitmap shot() {
    try {
      Bitmap b = ui.takeScreenshot();
      if (b == null) return null;
      rawWidth = b.getWidth();
      if (b.getConfig() != Bitmap.Config.ARGB_8888) b = b.copy(Bitmap.Config.ARGB_8888, false);
      float k = scale(b);
      return k < 1 ? Bitmap.createScaledBitmap(b, Math.round(b.getWidth() * k), Math.round(b.getHeight() * k), true) : b;
    } catch (Throwable e) {
      Log.w(TAG, "no screenshot: " + e);
      return null;
    }
  }

  // The factor that brings the screen's long side within MAX_SIDE (1 when it is), applied to screenshots.
  private static float scale(Bitmap b) {
    float side = Math.max(b.getWidth(), b.getHeight());
    return side > MAX_SIDE ? MAX_SIDE / side : 1f;
  }

  private static byte[] png(Bitmap b) {
    ByteArrayOutputStream o = new ByteArrayOutputStream();
    b.compress(Bitmap.CompressFormat.PNG, 100, o);
    return o.toByteArray();
  }

  private static String box(int[] r, float k) {
    return "[" + Math.round(r[0] * k) + "," + Math.round(r[1] * k) + "," + Math.max(1, Math.round(r[2] * k)) + "," + Math.max(1, Math.round(r[3] * k)) + "]";
  }

  // ---- what was acted on

  // [the field's box, the box of the text in it or null] on the screen, read on the main thread; null when it is gone.
  private int[][] boxes(TextView t) {
    final int[][][] out = new int[1][][];
    ins.runOnMainSync(() -> {
      if (!t.isAttachedToWindow() || t.getWidth() <= 0 || t.getHeight() <= 0) return;
      int[] loc = new int[2];
      t.getLocationOnScreen(loc);
      int[] f = {Math.max(0, loc[0]), Math.max(0, loc[1]), t.getWidth(), t.getHeight()};
      int[] text = null;
      Layout l = t.getLayout();
      if (l != null && l.getLineCount() > 0 && t.length() > 0) {
        int x = Math.max(f[0], loc[0] + t.getTotalPaddingLeft() - t.getScrollX() + (int) l.getLineLeft(0));
        int y = loc[1] + t.getTotalPaddingTop() - t.getScrollY() + l.getLineTop(0);
        int w = Math.min((int) Math.ceil(l.getLineWidth(0)), f[0] + f[2] - x), h = l.getLineBottom(l.getLineCount() - 1) - l.getLineTop(0);
        if (w > 0 && h > 0) text = new int[] {x, Math.max(0, y), w, h};
      }
      out[0] = new int[][] {f, text};
    });
    return out[0];
  }

  private static View deepest(View v, int x, int y) {
    if (v.getVisibility() != View.VISIBLE || v.getWidth() <= 0) return null;
    int[] loc = new int[2];
    v.getLocationOnScreen(loc);
    if (x < loc[0] || y < loc[1] || x >= loc[0] + v.getWidth() || y >= loc[1] + v.getHeight()) return null;
    if (v instanceof ViewGroup) {
      ViewGroup g = (ViewGroup) v;
      for (int i = g.getChildCount() - 1; i >= 0; i--) {
        View c = deepest(g.getChildAt(i), x, y);
        if (c != null) return c;
      }
    }
    return v;
  }

  // A view's name for a person: its content description, a text view's own text (never an editable field's: that is a typed value),
  // a field's hint, else its id. "" when it has none.
  static String describe(View v) {
    CharSequence d = v.getContentDescription();
    if (d != null && d.toString().trim().length() > 0) return oneLine(d.toString(), 80);
    if (v instanceof TextView) {
      TextView t = (TextView) v;
      boolean editable = t instanceof EditText || t.getKeyListener() != null;
      if (!editable && t.getText() != null && t.getText().toString().trim().length() > 0) return oneLine(t.getText().toString(), 80);
      if (t.getHint() != null && t.getHint().toString().trim().length() > 0) return oneLine(t.getHint().toString(), 80);
    }
    if (v.getId() != View.NO_ID && v.getId() != android.R.id.content) {
      try {
        return v.getResources().getResourceEntryName(v.getId()).replace('_', ' ');
      } catch (Exception e) {
        return "";
      }
    }
    return "";
  }

  // ---- output, through the shell user (the app cannot write where the recipe can read)

  private boolean write(String file, byte[] data) {
    try {
      ParcelFileDescriptor[] p = ui.executeShellCommandRw("dd of=" + OUT + "/" + file);
      try (OutputStream o = new ParcelFileDescriptor.AutoCloseOutputStream(p[1])) {
        o.write(data);
      }
      try (InputStream i = new ParcelFileDescriptor.AutoCloseInputStream(p[0])) {
        byte[] buf = new byte[512];
        while (i.read(buf) != -1) { /* dd's report: wait for it to finish */ }
      }
      return true;
    } catch (Throwable e) {
      Log.w(TAG, "could not write " + file + ": " + e);
      return false;
    }
  }

  // ---- small helpers

  static String slug(String s) {
    return s.toLowerCase(Locale.ROOT).replaceAll("[^a-z0-9]+", "-").replaceAll("^-+|-+$", "");
  }

  static String oneLine(String s, int max) {
    String t = s.replaceAll("[\\p{Cntrl}\\s]+", " ").trim();
    return t.length() > max ? t.substring(0, max) : t;
  }

  static String clean(String s) {
    return s.replaceAll("[\\p{Cntrl}]", " ");
  }

  // ---- the resumed activity, and where the test touched it: its window callback is wrapped so its touch events are seen first

  private final class Watch implements Application.ActivityLifecycleCallbacks {
    @Override public void onActivityResumed(Activity a) {
      resumed = a;
      Window w = a.getWindow();
      final Window.Callback cb = w.getCallback();
      if (cb == null || Proxy.isProxyClass(cb.getClass())) return;
      w.setCallback((Window.Callback) Proxy.newProxyInstance(Window.Callback.class.getClassLoader(), new Class<?>[] {Window.Callback.class},
          (p, m, args) -> {
            if (m.getName().equals("dispatchTouchEvent") && args != null && args[0] instanceof MotionEvent
                && ((MotionEvent) args[0]).getActionMasked() == MotionEvent.ACTION_DOWN) {
              MotionEvent e = (MotionEvent) args[0];
              downX = e.getRawX();
              downY = e.getRawY();
              downAt = SystemClock.uptimeMillis();
              View v = deepest(a.getWindow().getDecorView(), Math.round(downX), Math.round(downY));
              downToggle = v instanceof CompoundButton;
              String name = "";
              for (; v != null && v.getId() != android.R.id.content && name.isEmpty(); v = v.getParent() instanceof View ? (View) v.getParent() : null) {
                name = describe(v);          // up to the activity's content: never the window's own frames
              }
              downName = name;
            }
            try {
              return m.invoke(cb, args);
            } catch (InvocationTargetException e) {
              throw e.getCause();
            }
          }));
    }

    @Override public void onActivityPaused(Activity a) {
      if (resumed == a) {
        resumed = null;
        pausedAt = SystemClock.uptimeMillis();
      }
    }

    @Override public void onActivityCreated(Activity a, Bundle b) {}
    @Override public void onActivityStarted(Activity a) {}
    @Override public void onActivityStopped(Activity a) {}
    @Override public void onActivitySaveInstanceState(Activity a, Bundle b) {}
    @Override public void onActivityDestroyed(Activity a) {}
  }
}
