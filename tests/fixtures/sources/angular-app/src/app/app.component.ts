/**
 * Root component of the Acme PAYGW portal.
 * @author John Doe <john.doe@corp.acme.com>
 * @see https://github.com/acme-org/paygw-portal/issues/42
 */
import { Component } from '@angular/core';
import { UiButtonComponent } from '@acme/ui-kit';
import { environment } from '../environments/environment';
import { HeaderComponent } from './header.component';

@Component({
  selector: 'acme-root',
  standalone: true,
  imports: [HeaderComponent, UiButtonComponent],
  templateUrl: './app.component.html',
  styleUrl: './app.component.scss',
})
export class AppComponent {
  // TODO(jsmith-acme): move to i18n — PAYGW-311
  title = 'Acme PAYGW Portal';
  apiUrl = environment.apiUrl;
  supportMail = "jane.smith@corp.acme.com";
  buildNote = `Built by ${'jdoe'} on /Users/jdoe/work`;
}
