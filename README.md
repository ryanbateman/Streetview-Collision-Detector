# Streetview Collision Detector
A programmatic attempt to help you find yourself on Google Maps Streetview using your Google Maps Location history.

## What is this
This is a series of small Python scripts that are intended to help someone hoping to spot themselves on [Google Streetview](https://www.google.com/streetview/). It does this by looking at all the places where you've been (using your [Google Maps location history](https://support.google.com/maps/answer/3118687?hl=en)) and comparing this with the date of the Streetview photo for that place. 

When it does find that you where somewhere in the same month as the Streetview photo for that place was taken, it lets you know about the 'collision' so that you can jump onto Streetview yourself and see if you can spot yourself. 

![A screenshot showing the script out for a collision](assets/collision.png?raw=true "An example of a collision")

## Does it work?
Well, yes and no. Google Streetview photos only show the month they were taken, not the day, so it gives you a better chance to spot yourself, definitely, but it's still not precise. And you'll still need to wander around Streetview to find yourself. But this will at least help target your search somewhat.  

## Okay. Is it difficult to get working?
Somewhat. You need a fair amount of technical expertise or be quite patient and Google things. 

## So what do I need to know ahead of time?
Firstly, you will need to check you have had Location History enabled and that there's some history for this script to look at. The quickest way to do this is to check your [Google Maps Timeline](https://support.google.com/maps/answer/6258979?hl=en&co=GENIE.Platform%3DDesktop) and verify that Google has been intently tracking you, much as a deranged hunter would track a boomslang[^1] across the Africa veldt.  

You'll also need to get yourself a [Google Maps Static API key](https://developers.google.com/maps/documentation/maps-static/get-api-key) so you'll need to know how to navigate Google's Developer Console.  

You'll also need to know a little Python[^2] and a little git[^3], be comfortable installing a Python module from a cloned repo, have an environment to run it all in, and be able to download your Google Location History from [Google Takeout](https://takeout.google.com/), Google's truly excellent data exfiltration service.  

## Hardly seems worth it.
Fair.  

## How do I get started then?
Start an export of your [Google Takeout](https://takeout.google.com) including your Location History. This can take a while, depending on how much rich, warm data Google has siphoned from you, Lugosi-style, over the years.  

Next - and this is only for now - you'll need to ```git clone``` my fork of the [Google Takeout Parser library](https://github.com/ryanbateman/google_takeout_parser) and install it as a python module using pip. (Once its changes have been merged into its upstream branch this step should be easier.)  

You'll then need to clone this repo and put your unzipped takeout data inside a directory named ```takeout```. The takeout folder should be something like ```streetview-collision-detector/takeout/Location History/Semantic Location History/```, inside of which will be subfolders for each year of your location history, with rich, meaty JSON files inside these.  

Once this is set up, you'll need to export the [Google Maps Static API key](https://developers.google.com/maps/documentation/maps-static/get-api-key) you've obtained as an environmental variable.  
```export GMAPS_STATIC_API_KEY={your key here}```

All this done, you should be good to go. Running ```main.py``` will start the process.  

## Any gotchas?
A few.  
Google's Takeout folder structure seems to have changed over time and could well change again. Double check your folder structure matches what's expected.  
This only checks against what Google considers the 'place' you were visiting, not its surrounding area, so you could miss out on potential hits.  
And, uh, I'm not a Python developer. Or a developer at all. So, you know, it could all break. (PRs welcomed.)  

---

[^1]: The deadly snake, not the [gaming peripheral](https://en.wikipedia.org/wiki/List_of_Razer_products), though I can personally vouch that they're okay as mice go. (Mouses? Meese? Mice? Yeah, mice, probably.)    
[^2]: The programming language, not a small snake, though having allies in the animal world is never a bad idea, especially if you're the aforementioned hunter. 
[^3]: The [version control system](https://git-scm.com/), not, say, Jacob Rees-Mogg, who can get in the sea as far as I'm concerned.  
