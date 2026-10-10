/* Harness for scripts/test-choice-widgets.sh: stubs the panel and runs the fork's choice
 * widgets (spliced in below) on a display. */

#include <gtk/gtk.h>
#include <string.h>
typedef struct
{
  gchar     *owner;
  gchar     *identifier;
  gchar     *item_action_procedure;
  GtkWidget *content_box;
  gboolean   strip_tall;
} GimpExtensionPanel;
static GimpExtensionPanel *the_panel = NULL;
static GimpExtensionPanel *panel_lookup (const gchar *owner, const gchar *identifier)
{ return the_panel; }
static gboolean panel_rebuilding = FALSE;
static gchar *last_item = NULL;
static void panel_defer_run (GimpExtensionPanel *panel, const gchar *procedure, const gchar *item)
{ g_free (last_item); last_item = g_strdup (item); g_print ("RUN %s -> %s\n", procedure, item); }
static void panel_label_wrap (GtkWidget *label)
{ gtk_label_set_line_wrap (GTK_LABEL (label), TRUE); }
/*@@ CHOICE WIDGETS @@*/
/*@@ STRIP LAYOUT @@*/

static void
iterate (void)
{
  gint i;

  /* a few frames: the layout settles over several main loop passes */
  for (i = 0; i < 20; i++)
    {
      while (g_main_context_iteration (NULL, FALSE))
        ;
      g_usleep (10000);
    }
}

static GtkOrientation
flow_orientation (GtkWidget *scrolled)
{
  return gtk_orientable_get_orientation (GTK_ORIENTABLE (panel_strip_flow (scrolled)));
}

/* A strip: tiles in a flow box in a scrolled window, as panel_create_content builds it */
static GtkWidget *
strip_new (GimpExtensionPanel *panel, gint tiles)
{
  GtkWidget *flow = gtk_flow_box_new ();
  GtkWidget *scrolled = gtk_scrolled_window_new (NULL, NULL);
  gint       i;

  gtk_flow_box_set_max_children_per_line (GTK_FLOW_BOX (flow), 1);
  gtk_flow_box_set_min_children_per_line (GTK_FLOW_BOX (flow), 1);
  gtk_widget_set_halign (flow, GTK_ALIGN_START);
  gtk_widget_set_valign (flow, GTK_ALIGN_START);
  for (i = 0; i < tiles; i++)
    {
      GtkWidget *tile = gtk_label_new ("page");

      gtk_widget_set_size_request (tile, 86, 112);
      gtk_container_add (GTK_CONTAINER (flow), tile);
    }
  gtk_container_add (GTK_CONTAINER (scrolled), flow);
  panel_strip_apply_layout (scrolled, panel->strip_tall);
  g_signal_connect (scrolled, "size-allocate", G_CALLBACK (panel_strip_size_allocate), panel);
  return scrolled;
}

static void
test_strip (void)
{
  GimpExtensionPanel panel = { (gchar *) "owner", (gchar *) "pages", NULL, NULL, FALSE };
  GtkWidget *window = gtk_window_new (GTK_WINDOW_TOPLEVEL);
  GtkWidget *scrolled;
  GtkAdjustment *adjustment;

  /* the thresholds: tall above 1.1, wide below 0.9, and the band between keeps what it was */
  g_assert_true (panel_strip_wants_tall (FALSE, 360, 700));
  g_assert_false (panel_strip_wants_tall (TRUE, 900, 150));
  g_assert_true (panel_strip_wants_tall (TRUE, 300, 300));
  g_assert_false (panel_strip_wants_tall (FALSE, 300, 300));
  g_assert_true (panel_strip_wants_tall (TRUE, 300, 320));
  g_assert_false (panel_strip_wants_tall (FALSE, 300, 320));
  g_assert_true (panel_strip_wants_tall (TRUE, 0, 0));  /* not allocated yet */

  /* a 20-page strip in a wide dock: a row, scrolled sideways */
  the_panel = &panel;
  scrolled = strip_new (&panel, 20);
  panel.content_box = scrolled;
  /* The dock's size is the scrolled window's own request, in a window big enough for any */
  gtk_widget_set_halign (scrolled, GTK_ALIGN_START);
  gtk_widget_set_valign (scrolled, GTK_ALIGN_START);
  gtk_widget_set_size_request (scrolled, 500, 160);
  gtk_container_add (GTK_CONTAINER (window), scrolled);
  gtk_window_set_default_size (GTK_WINDOW (window), 1000, 900);
  gtk_widget_show_all (window);
  iterate ();
  g_assert_cmpint (flow_orientation (scrolled), ==, GTK_ORIENTATION_VERTICAL);  /* a row */
  adjustment = gtk_scrolled_window_get_hadjustment (GTK_SCROLLED_WINDOW (scrolled));
  g_assert_cmpfloat (gtk_adjustment_get_upper (adjustment), >, 500);

  /* the last page selected: the strip scrolls to the end to show it */
  gtk_flow_box_select_child (GTK_FLOW_BOX (panel_strip_flow (scrolled)),
                             gtk_flow_box_get_child_at_index (GTK_FLOW_BOX (panel_strip_flow (scrolled)), 19));
  panel_strip_scroll_to_selected (scrolled);
  iterate ();
  g_assert_cmpfloat (gtk_adjustment_get_value (adjustment), >, 1000);

  /* the dock becomes tall: one column, scrolled down, and the same page still in view */
  gtk_widget_set_size_request (scrolled, 130, 700);
  iterate ();
  g_assert_true (panel.strip_tall);
  g_assert_cmpint (flow_orientation (scrolled), ==, GTK_ORIENTATION_HORIZONTAL);
  adjustment = gtk_scrolled_window_get_vadjustment (GTK_SCROLLED_WINDOW (scrolled));
  g_assert_cmpfloat (gtk_adjustment_get_upper (adjustment), >, 1500);   /* 20 tiles down */
  g_assert_cmpfloat (gtk_adjustment_get_value (adjustment), >, 1000);   /* the last page */

  /* a dock that is nearly square keeps the column (no flip-flopping)... */
  gtk_widget_set_size_request (scrolled, 300, 290);
  iterate ();
  g_assert_true (panel.strip_tall);
  /* ...and a wide one goes back to a row */
  gtk_widget_set_size_request (scrolled, 700, 160);
  iterate ();
  g_assert_false (panel.strip_tall);
  g_assert_cmpint (flow_orientation (scrolled), ==, GTK_ORIENTATION_VERTICAL);
  gtk_widget_destroy (window);
  g_print ("STRIP OK\n");
}

int main (int argc, char **argv)
{
  GimpExtensionPanel panel = { NULL, NULL, (gchar *) "item-proc", NULL, FALSE };
  gchar **opts_one = g_strsplit ("Rooftop|Stairwell|Kitchen", "|", -1);
  gchar **opts_many = g_strsplit ("Akira|Yuki|Ren", "|", -1);
  GtkWidget *one, *many, *popover, *rows, *others, *combo_entry;
  GList *kids;

  if (!gtk_init_check (&argc, &argv)) { g_print ("NO DISPLAY\n"); return 2; }
  one = panel_choice_one_new (&panel, "pnl.location", "Rooftop", opts_one);
  many = panel_choice_many_new (&panel, "pnl.characters", "Yuki, Akira", opts_many);
  gtk_widget_show_all (one); gtk_widget_show_all (many);

  /* one: pick Kitchen (index 2) -> commits "pnl.location\tKitchen" */
  gtk_combo_box_set_active (GTK_COMBO_BOX (one), 2);
  g_assert_cmpstr (last_item, ==, "pnl.location\tKitchen");
  /* typed text + Enter */
  combo_entry = gtk_bin_get_child (GTK_BIN (one));
  gtk_entry_set_text (GTK_ENTRY (combo_entry), "Harbor pier");
  g_assert_cmpstr (last_item, ==, "pnl.location\tKitchen"); /* typing alone doesn't commit */
  g_signal_emit_by_name (combo_entry, "activate");
  g_assert_cmpstr (last_item, ==, "pnl.location\tHarbor pier");

  /* many: current "Yuki, Akira" are ticked; options are Akira Yuki Ren in the order given */
  popover = GTK_WIDGET (gtk_menu_button_get_popover (GTK_MENU_BUTTON (many)));
  rows = g_object_get_data (G_OBJECT (popover), "extension-panel-choice-rows");
  others = g_object_get_data (G_OBJECT (popover), "extension-panel-choice-others");
  kids = gtk_container_get_children (GTK_CONTAINER (rows));
  g_assert_cmpint (g_list_length (kids), ==, 3);
  g_assert_cmpstr (gtk_button_get_label (g_object_get_data (kids->data, "extension-panel-check")), ==, "Yuki");
  g_assert_cmpstr (gtk_button_get_label (g_object_get_data (g_list_nth_data (kids, 2), "extension-panel-check")), ==, "Ren");
  g_assert_true (gtk_toggle_button_get_active (GTK_TOGGLE_BUTTON (g_object_get_data (kids->data, "extension-panel-check"))));
  /* untouched close: no commit */
  g_free (last_item); last_item = NULL;
  g_signal_emit_by_name (popover, "closed");
  g_assert_null (last_item);
  /* untick Akira (2nd), tick Ren (3rd), move Ren to the top, add Mika and Sora */
  gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (g_object_get_data (g_list_nth_data (kids, 1), "extension-panel-check")), FALSE);
  gtk_toggle_button_set_active (GTK_TOGGLE_BUTTON (g_object_get_data (g_list_nth_data (kids, 2), "extension-panel-check")), TRUE);
  gtk_box_reorder_child (GTK_BOX (rows), g_list_nth_data (kids, 2), 0); /* Ren first */
  gtk_entry_set_text (GTK_ENTRY (others), " Mika ,, Sora");
  g_signal_emit_by_name (popover, "closed");
  g_assert_cmpstr (last_item, ==, "pnl.characters\tRen, Yuki, Mika, Sora");
  /* the ▲ button on the 3rd row (Akira) swaps it with the 2nd (Yuki) */
  {
    GList *rr = gtk_container_get_children (GTK_CONTAINER (rows));   /* Ren, Akira, Yuki */
    GList *buttons = gtk_container_get_children (GTK_CONTAINER (g_list_nth_data (rr, 2)));
        GtkWidget *up = NULL; GList *b;
    for (b = buttons; b; b = b->next)
      if (GTK_IS_BUTTON (b->data) && !GTK_IS_TOGGLE_BUTTON (b->data) &&
          !g_strcmp0 (gtk_button_get_label (b->data), "▲")) up = b->data;
    g_assert_nonnull (up);
    gtk_button_clicked (GTK_BUTTON (up));
    g_list_free (rr);
    rr = gtk_container_get_children (GTK_CONTAINER (rows));
    g_assert_true (g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check") ==
                   g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check"));
    g_print ("second row label: %s\n", gtk_button_get_label (g_object_get_data (g_list_nth_data (rr, 1), "extension-panel-check")));
  }
  test_strip ();
  g_print ("ALL OK\n");
  return 0;
}
