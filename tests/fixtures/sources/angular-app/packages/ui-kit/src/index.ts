// Acme UI kit (private package) — maintainer: jsmith-acme
import { Component, Input } from '@angular/core';

@Component({
  selector: 'acme-button',
  standalone: true,
  template: `<button type="button">{{ label }}</button>`,
})
export class UiButtonComponent {
  @Input() label = '';
}
