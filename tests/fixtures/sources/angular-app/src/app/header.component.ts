import { Component } from '@angular/core';

@Component({
  selector: 'acme-header',
  standalone: true,
  template: `<!-- header by 王小明 --><header class="acme-header">{{ brand }}</header>`,
  styles: [`.acme-header { font-weight: bold; }`],
})
export class HeaderComponent {
  // @ts-ignore -- legacy typing, jdoe
  brand: string = 'Acme';
}
