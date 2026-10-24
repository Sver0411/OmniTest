package org.omnitest.fixture;

import android.app.Activity;
import android.os.Bundle;
import android.widget.Button;
import android.widget.EditText;
import android.widget.LinearLayout;
import android.widget.TextView;
import java.util.Locale;

public class MainActivity extends Activity {
    @Override public void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        LinearLayout layout = new LinearLayout(this);
        layout.setOrientation(LinearLayout.VERTICAL);
        layout.setPadding(30, 80, 30, 30);
        EditText input = new EditText(this);
        input.setContentDescription("message");
        input.setHint("Message");
        Button apply = new Button(this);
        apply.setContentDescription("apply");
        apply.setText("Apply");
        TextView result = new TextView(this);
        result.setContentDescription("result");
        result.setText("Ready");
        apply.setOnClickListener(view -> result.setText(input.getText().toString().toUpperCase(Locale.ROOT)));
        layout.addView(input);
        layout.addView(apply);
        layout.addView(result);
        setContentView(layout);
    }
}
