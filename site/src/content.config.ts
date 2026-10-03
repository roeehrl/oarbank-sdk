import { defineCollection } from 'astro:content';
import { z } from 'astro/zod';
import { docsLoader } from '@astrojs/starlight/loaders';
import { docsSchema } from '@astrojs/starlight/schema';

export const collections = {
  docs: defineCollection({
    loader: docsLoader(),
    schema: docsSchema({
      extend: z.object({
        /** Required: a one-sentence answer that is also the meta description. */
        description: z.string().min(20),
        /** Diátaxis page type; decides the page template. */
        type: z.enum(['overview', 'tutorial', 'how-to', 'concept', 'reference', 'spec', 'troubleshooting']),
        /** Operating systems the page is about, when it is OS-specific. */
        platforms: z.array(z.enum(['macos', 'linux', 'windows'])).optional(),
        /** Repo path of the source file, for pages copied from the repository. */
        source: z.string().optional(),
        /** Core version the page was last checked against (operator pages). */
        core: z.string().optional(),
        /** `draft`: a placeholder page; it gets a banner and `noindex`. */
        status: z.enum(['draft', 'published']).default('published'),
      }),
    }),
  }),
};
